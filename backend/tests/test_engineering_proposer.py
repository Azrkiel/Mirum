"""Offline provider/transport fixtures; these do not qualify live AI accuracy."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from backend.tests.test_engineering_contracts import data, review_for


def response(**updates):
    result = dict(schema_version='engineering_proposal_v1', state='proposed', fields=data()['content'],
                  questions=[], unsupported_features=[], contradictions=[])
    result.update(updates)
    return json.dumps(result)


class FixtureProvider:
    available = True
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []
    def settings(self):
        from engineering.proposer import ProviderSettings
        return ProviderSettings(provider='fixture', sdk_version='offline-v1', model='fixture-model',
                                temperature=0.0, max_tokens=4096)
    async def generate(self, prompt, schema, settings):
        from engineering.proposer import ProviderReply
        self.calls.append((prompt, schema, settings))
        output = self.outputs.pop(0)
        if isinstance(output, BaseException): raise output
        return ProviderReply(text=output, prompt_tokens=10, output_tokens=20)


def setup(provider, **kwargs):
    from engineering.briefs import BriefService, MemoryBriefRepository
    from engineering.proposer import BriefProposer, MemoryProposalLedger
    service = BriefService(MemoryBriefRepository())
    initial = service.create({})
    ledger = MemoryProposalLedger()
    proposer = BriefProposer(service, provider=provider, ledger=ledger, **kwargs)
    return service, initial.draft, proposer, ledger


def propose(proposer, draft):
    return asyncio.run(proposer.propose(draft.draft_id, expected_revision=draft.revision,
                                       prompt='Reference fixture prompt; all output fields still require review'))


def test_valid_proposal_is_reviewable_data_and_needs_exact_acknowledgement():
    from engineering.contracts import REVIEW_FIELDS
    provider = FixtureProvider([response()])
    service, draft, proposer, ledger = setup(provider)
    result = propose(proposer, draft)
    assert result.state == 'proposed' and result.prepared.draft.revision == 2
    proposed = result.prepared.draft
    assert proposed.provenance.input_mode == 'ai'
    assert {o.field for o in proposed.provenance.field_origins} == set(REVIEW_FIELDS)
    assert all(o.origin == 'ai_proposed' for o in proposed.provenance.field_origins)
    brief = service.finalize(review_for(proposed))
    from engineering.cantilever import CantileverAdapter
    assert len(CantileverAdapter().candidates(brief)) == 3
    assert len(provider.calls) == 1
    audit = ledger.read(draft.draft_id, 1)
    assert audit.calls == 1 and audit.prompt_tokens == 10 and audit.output_tokens == 20
    assert audit.request_sha256 == hashlib.sha256(b'Reference fixture prompt; all output fields still require review').hexdigest()
    assert all(audit.request_sha256 in origin.reference for origin in proposed.provenance.field_origins)
    assert 'Reference fixture prompt' not in audit.model_dump_json()


@pytest.mark.parametrize('kind', ['ambiguous', 'contradictory', 'unsupported', 'partial'])
def test_incomplete_or_unsupported_proposals_cannot_finalize(kind):
    fields = data()['content']
    values = {}
    if kind == 'ambiguous': values = dict(state='clarification_required', questions=['Specify force in N; kg is mass'])
    if kind == 'contradictory': values = dict(contradictions=['Two different lengths were requested'])
    if kind == 'unsupported': values = dict(state='unsupported', unsupported_features=['holes'])
    if kind == 'partial': del fields['requirements']['material']['reference']
    provider = FixtureProvider([response(fields=fields, **values)])
    service, draft, proposer, _ = setup(provider)
    result = propose(proposer, draft)
    assert result.state != 'proposed'
    assert result.prepared.draft.clarifications
    assert service.export(draft.draft_id)['issues']
    if result.prepared.draft.content is not None:
        with pytest.raises(ValueError): service.finalize(review_for(result.prepared.draft))


@pytest.mark.parametrize('bad', ['not JSON', '{"script":"import os"}', '[]'])
def test_one_repair_only_for_malformed_response(bad):
    provider = FixtureProvider([bad, response()])
    _, draft, proposer, ledger = setup(provider)
    assert propose(proposer, draft).state == 'proposed'
    assert len(provider.calls) == 2 and ledger.read(draft.draft_id, 1).calls == 2
    assert bad not in provider.calls[1][0]  # never echo unbounded/raw model output
    from engineering.briefs import RevisionConflict
    with pytest.raises(RevisionConflict): propose(proposer, draft)
    assert len(provider.calls) == 2


def test_repeated_malformed_response_is_visible_and_form_still_works():
    provider = FixtureProvider(['bad', 'bad', response()])
    service, draft, proposer, ledger = setup(provider)
    result = propose(proposer, draft)
    assert result.state == 'unavailable' and len(provider.calls) == 2
    assert service.read(draft.draft_id).draft.revision == 1
    edited = service.edit(draft.draft_id, expected_revision=1, payload=data()['content'])
    assert service.finalize(review_for(edited.draft))


@pytest.mark.parametrize('kind', ['missing_credentials', 'provider_error', 'oversized'])
def test_provider_failure_and_response_budget_leave_form_available(kind):
    provider = FixtureProvider([RuntimeError('private provider exception') if kind == 'provider_error' else 'x'*16385])
    if kind == 'missing_credentials': provider.available = False
    service, draft, proposer, _ = setup(provider)
    result = propose(proposer, draft)
    assert result.state == 'unavailable'
    assert 'private provider exception' not in str(result)
    assert len(provider.calls) == (0 if kind == 'missing_credentials' else 1)
    prepared = service.edit(draft.draft_id, expected_revision=1, payload=data()['content'])
    assert service.finalize(review_for(prepared.draft))


def test_prompt_budget_checked_before_provider_and_no_clipping():
    provider = FixtureProvider([response()])
    _, draft, proposer, _ = setup(provider)
    with pytest.raises(ValueError):
        asyncio.run(proposer.propose(draft.draft_id, expected_revision=1, prompt='x'*8001))
    assert provider.calls == []


@pytest.mark.parametrize('mode', ['timeout', 'hook', 'task'])
def test_deadline_and_cancellation_await_provider_cleanup(mode):
    cleaned = []
    class Waiting(FixtureProvider):
        async def generate(self, *args):
            self.calls.append(args)
            try: await asyncio.sleep(30)
            finally: cleaned.append(True)
    provider = Waiting([])
    service, draft, proposer, ledger = setup(provider, call_seconds=0.1 if mode == 'timeout' else 45)
    async def run():
        event = asyncio.Event()
        task = asyncio.create_task(proposer.propose(draft.draft_id, expected_revision=1, prompt='fixture', cancelled=event.is_set))
        await asyncio.sleep(0.05)
        if mode == 'hook': event.set()
        if mode == 'task': task.cancel()
        if mode == 'task':
            with pytest.raises(asyncio.CancelledError): await task
        else:
            result = await task
            assert result.state == ('cancelled' if mode == 'hook' else 'unavailable')
    asyncio.run(run())
    assert cleaned == [True] and len(provider.calls) == 1
    assert service.read(draft.draft_id).draft.revision == 1
    assert ledger.read(draft.draft_id, 1).calls == 1


def test_finalized_revision_cannot_start_proposal_and_execution_has_no_provider(monkeypatch):
    provider = FixtureProvider([response()])
    service, draft, proposer, _ = setup(provider)
    prepared = service.edit(draft.draft_id, expected_revision=1, payload=data()['content'])
    brief = service.finalize(review_for(prepared.draft))
    from engineering.briefs import RevisionConflict
    with pytest.raises(RevisionConflict): propose(proposer, prepared.draft)
    assert provider.calls == []
    from engineering.search import search
    result = asyncio.run(search(brief, cancelled=lambda: True))
    assert result.state == 'cancelled' and provider.calls == []


def test_sdk_schema_builds_offline_and_transport_disables_retries(monkeypatch):
    from engineering.proposer import GeminiProposalProvider, provider_schema
    from google.ai import generativelanguage_v1beta as glm
    assert glm.Schema(provider_schema())
    captured = []
    class Transport:
        async def close(self): captured.append('closed')
    class Client:
        transport = Transport()
        def __init__(self, **kwargs): pass
        async def generate_content(self, request, **kwargs):
            assert kwargs == {'retry': None, 'timeout': 45}
            assert request.model == 'models/gemini-3.6-flash'
            assert request.generation_config.max_output_tokens == 4096
            captured.append(request)
            return glm.GenerateContentResponse(candidates=[dict(finish_reason='STOP', content=dict(parts=[dict(text=response())]))],
                usage_metadata=dict(prompt_token_count=10, candidates_token_count=20))
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    monkeypatch.setattr(glm, 'GenerativeServiceAsyncClient', Client)
    provider = GeminiProposalProvider()
    result = asyncio.run(provider.generate('fixture', provider_schema(), provider.settings()))
    assert result.prompt_tokens == 10 and result.output_tokens == 20
    assert captured[-1] == 'closed'


def test_checked_preparation_selects_36_flash_without_changing_legacy_models():
    import config
    from subagents.model_registry import ModelRegistry
    from engineering.proposer import GeminiProposalProvider
    registry = ModelRegistry.instance()
    legacy = {name: registry.get(name).model_id for name in ('clarifier', 'classifier', 'machinist')}
    settings = GeminiProposalProvider().settings()
    assert settings.model == 'gemini-3.6-flash'
    assert settings.temperature == 0.4 and settings.max_tokens == 4096
    assert registry.get('clarifier').max_tokens == 1024
    assert config.GEMINI_MODEL_LITE == config.GEMINI_MODEL_HEAVY == 'gemini-3.5-flash'
    assert {name: registry.get(name).model_id for name in legacy} == legacy


def test_gemini_truncated_candidate_stops_without_schema_repair(monkeypatch):
    from google.ai import generativelanguage_v1beta as glm
    from engineering.proposer import GeminiProposalProvider
    events = []
    class Transport:
        async def close(self): events.append('closed')
    class Client:
        transport = Transport()
        def __init__(self, **kwargs): pass
        async def generate_content(self, request, **kwargs):
            events.append('called')
            assert kwargs == {'retry': None, 'timeout': 45}
            return glm.GenerateContentResponse(candidates=[dict(finish_reason='MAX_TOKENS',
                content=dict(parts=[dict(text='private incomplete provider reply')]))])
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    monkeypatch.setattr(glm, 'GenerativeServiceAsyncClient', Client)
    _, draft, proposer, ledger = setup(GeminiProposalProvider())
    result = propose(proposer, draft)
    assert result.state == 'unavailable'
    assert events == ['called', 'closed'] and ledger.read(draft.draft_id, 1).calls == 1
    assert 'private incomplete' not in str(result) + ledger.read(draft.draft_id, 1).model_dump_json()


def test_gemini_transport_uses_installed_os_trust_without_global_override(monkeypatch):
    import ssl
    import grpc
    from google.ai import generativelanguage_v1beta as glm
    from google.ai.generativelanguage_v1beta.services.generative_service import transports
    from engineering.proposer import GeminiProposalProvider, provider_schema
    captured = {}
    roots = ssl.create_default_context().get_ca_certs(binary_form=True)
    expected = ''.join(ssl.DER_cert_to_PEM_cert(root) for root in roots).encode('ascii')
    sentinel = object()
    def credentials(*, root_certificates):
        captured['roots'] = root_certificates
        return sentinel
    class Transport:
        def __init__(self, **kwargs): captured['transport'] = kwargs
        async def close(self): captured['closed'] = True
    class Client:
        def __init__(self, *, transport, client_options):
            self.transport = transport(host='offline.invalid', credentials='sdk-owned')
        async def generate_content(self, request, **kwargs):
            raise RuntimeError('offline provider failure')
    monkeypatch.delenv('GRPC_DEFAULT_SSL_ROOTS_FILE_PATH', raising=False)
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    monkeypatch.setattr(grpc, 'ssl_channel_credentials', credentials)
    monkeypatch.setattr(transports, 'GenerativeServiceGrpcAsyncIOTransport', Transport)
    monkeypatch.setattr(glm, 'GenerativeServiceAsyncClient', Client)
    provider = GeminiProposalProvider()
    with pytest.raises(RuntimeError, match='offline provider failure'):
        asyncio.run(provider.generate('fixture', provider_schema(), provider.settings()))
    assert captured['roots'] == expected and expected
    assert captured['transport']['ssl_channel_credentials'] is sentinel
    assert captured['closed'] is True
    import os
    assert 'GRPC_DEFAULT_SSL_ROOTS_FILE_PATH' not in os.environ


def test_versioned_corpus_has_ten_complete_ten_ambiguous_ten_unsupported():
    from engineering.contracts import BriefContent
    corpus = json.loads((Path(__file__).with_name('fixtures')/'engineering'/'ai-corpus-v1.json').read_text())
    assert corpus['version'] == 'engineering_ai_corpus_v1'
    cases = corpus['cases']
    assert len({c['id'] for c in cases}) == len(cases) == 30
    assert {k: sum(c['kind'] == k for c in cases) for k in ('complete', 'ambiguous', 'unsupported')} == {
        'complete': 10, 'ambiguous': 10, 'unsupported': 10}
    for case in cases:
        if case['kind'] == 'complete': BriefContent.model_validate_json(json.dumps(case['expected_content']))


CORPUS_PATH = Path(__file__).with_name('fixtures')/'engineering'/'ai-corpus-v1.json'


@pytest.mark.parametrize('case', json.loads(CORPUS_PATH.read_text())['cases'], ids=lambda c: c['id'])
def test_corpus_oracle_fixtures_use_common_validation_without_execution(case):
    provider = FixtureProvider([json.dumps(case['fixture_response'])])
    service, draft, proposer, _ = setup(provider)
    result = asyncio.run(proposer.propose(draft.draft_id, expected_revision=1, prompt=case['prompt']))
    assert result.state == case['expected_state']
    assert len(provider.calls) == 1
    if result.state == 'proposed':
        from engineering.contracts import BriefContent
        assert result.prepared.draft.content == BriefContent.model_validate_json(json.dumps(case['expected_content']))
    else:
        assert result.prepared.draft.clarifications


def test_provider_configuration_failure_has_safe_visible_outcome():
    class Broken(FixtureProvider):
        def settings(self): raise RuntimeError('private configuration exception')
    service, draft, proposer, _ = setup(Broken([]))
    result = propose(proposer, draft)
    assert result.state == 'unavailable' and 'private configuration exception' not in str(result)
    assert service.edit(draft.draft_id, expected_revision=1, payload=data()['content'])


def test_shared_ledger_prevents_concurrent_duplicate_calls():
    from engineering.proposer import BriefProposer
    from engineering.briefs import RevisionConflict
    entered = asyncio.Event()
    release = asyncio.Event()
    class Waiting(FixtureProvider):
        async def generate(self, *args):
            entered.set()
            await release.wait()
            return await super().generate(*args)
    provider = Waiting([response()])
    service, draft, first, ledger = setup(provider)
    second = BriefProposer(service, provider=provider, ledger=ledger)
    async def run():
        task = asyncio.create_task(first.propose(draft.draft_id, expected_revision=1, prompt='fixture'))
        await entered.wait()
        with pytest.raises(RevisionConflict):
            await second.propose(draft.draft_id, expected_revision=1, prompt='fixture')
        release.set()
        assert (await task).state == 'proposed'
    asyncio.run(run())
    assert len(provider.calls) == 1 and ledger.read(draft.draft_id, 1).calls == 1


def test_edit_during_provider_call_cannot_overwrite_the_new_revision():
    from engineering.briefs import RevisionConflict
    class Editing(FixtureProvider):
        async def generate(self, *args):
            service.edit(draft.draft_id, expected_revision=1, payload=data()['content'])
            return await super().generate(*args)
    provider = Editing([response()])
    service, draft, proposer, ledger = setup(provider)
    with pytest.raises(RevisionConflict): propose(proposer, draft)
    assert service.read(draft.draft_id).draft.revision == 2
    assert ledger.read(draft.draft_id, 1).status == 'conflict'


def test_finalized_ai_and_form_semantics_match_and_native_execution_never_calls_ai():
    from engineering.briefs import BriefService, MemoryBriefRepository
    from engineering.search import search
    provider = FixtureProvider([response()])
    service, draft, proposer, _ = setup(provider)
    proposed = propose(proposer, draft).prepared.draft
    ai_brief = service.finalize(review_for(proposed))
    typed = BriefService(MemoryBriefRepository())
    form_draft = typed.create(data()['content']).draft
    form_brief = typed.finalize(review_for(form_draft))
    assert ai_brief.brief_sha256 == form_brief.brief_sha256
    assert ai_brief.fixed_requirements_sha256 == form_brief.fixed_requirements_sha256
    result = asyncio.run(search(ai_brief))
    assert result.state == 'selected' and result.selected['candidate'].specification.height_mm == 15
    assert len(provider.calls) == 1


def test_finalization_during_settings_wins_before_any_provider_call():
    from engineering.briefs import RevisionConflict
    class Finalizing(FixtureProvider):
        def settings(self):
            service.finalize(review_for(prepared.draft))
            return super().settings()
    provider = Finalizing([response()])
    service, _, proposer, _ = setup(provider)
    prepared = service.create(data()['content'])
    with pytest.raises(RevisionConflict): propose(proposer, prepared.draft)
    assert provider.calls == []


def test_active_proposal_reservation_blocks_finalization_until_cleanup():
    from engineering.briefs import RevisionConflict
    class Finalizing(FixtureProvider):
        async def generate(self, *args):
            with pytest.raises(RevisionConflict): service.finalize(review_for(prepared.draft))
            return await super().generate(*args)
    provider = Finalizing(['bad', response()])
    service, _, proposer, _ = setup(provider)
    prepared = service.create(data()['content'])
    result = propose(proposer, prepared.draft)
    assert result.state == 'proposed' and len(provider.calls) == 2
    assert service.finalize(review_for(result.prepared.draft))


def test_newer_form_revision_finalization_prevents_repair_of_old_revision():
    from engineering.briefs import RevisionConflict
    class Editing(FixtureProvider):
        async def generate(self, *args):
            edited = service.edit(draft.draft_id, expected_revision=1, payload=data()['content'])
            service.finalize(review_for(edited.draft))
            return await super().generate(*args)
    provider = Editing(['bad', response()])
    service, draft, proposer, _ = setup(provider)
    with pytest.raises(RevisionConflict): propose(proposer, draft)
    assert len(provider.calls) == 1


def test_proposal_schema_and_typed_service_import_without_provider_or_native():
    code = '''
import sys
from engineering.proposer import ProposalResponse, provider_schema
ProposalResponse.model_json_schema()
provider_schema()
assert not any(n in sys.modules for n in ['agents', 'google.generativeai', 'google.ai.generativelanguage_v1beta', 'cadquery', 'OCP', 'numpy'])
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('seconds', [46, 0, -1, True, float('nan'), float('inf')])
def test_call_deadline_cannot_be_disabled_or_increased(seconds):
    with pytest.raises(ValueError): setup(FixtureProvider([]), call_seconds=seconds)


def test_failed_validation_is_kept_in_the_audit_ledger_only(caplog):
    raw_bad = '{"schema_version": "engineering_proposal_v1", "state": "bogus-secret-marker"}'
    provider = FixtureProvider([raw_bad, 'not json at all'])
    _, draft, proposer, ledger = setup(provider)
    with caplog.at_level('DEBUG'):
        result = propose(proposer, draft)
    assert result.state == 'unavailable'
    # never returned to the client-facing result, never logged
    assert 'bogus-secret-marker' not in result.model_dump_json()
    assert 'bogus-secret-marker' not in caplog.text and 'not json at all' not in caplog.text
    audit = ledger.read(draft.draft_id, draft.revision)
    first, second = audit.validation_failures
    assert (first.attempt, second.attempt) == (1, 2)
    assert first.raw_reply == raw_bad and not first.raw_truncated
    pairs = {(e.location, e.type) for e in first.errors}
    assert ('state', 'literal_error') in pairs
    assert ('fields', 'missing') in pairs
    assert all('bogus-secret-marker' not in e.location + e.type for e in first.errors)
    assert [(e.location, e.type) for e in second.errors] == [('line 1 column 1', 'json_invalid')]
    assert second.raw_reply == 'not json at all' and not second.raw_truncated


def test_audited_raw_reply_is_bounded():
    from engineering.proposer import validation_failure
    failure = validation_failure(1, ValueError('x'), 'y' * 30000)
    assert len(failure.raw_reply) == 16384 and failure.raw_truncated


def test_valid_reply_records_no_validation_failure():
    _, draft, proposer, ledger = setup(FixtureProvider([response()]))
    assert propose(proposer, draft).state == 'proposed'
    assert ledger.read(draft.draft_id, draft.revision).validation_failures == ()


def test_flag_off_builds_no_provider_and_reads_no_key(monkeypatch):
    from engineering import proposer as module
    from engineering.briefs import BriefService, MemoryBriefRepository
    reads = []
    class Watching(dict):
        def get(self, key, *args): reads.append(key); return super().get(key, *args)
        def __getitem__(self, key): reads.append(key); return super().__getitem__(key)
        def __contains__(self, key): reads.append(key); return super().__contains__(key)
    monkeypatch.setattr(module.os, 'environ', Watching({'GEMINI_API_KEY_HEAVY': 'must-not-be-read'}))
    built = []
    monkeypatch.setattr(module.GeminiProposalProvider, '__init__', lambda self: built.append(1))
    service = BriefService(MemoryBriefRepository())
    assert module.make_proposer(service, ledger=module.MemoryProposalLedger(), enabled=False,
                                provider_factory=lambda: built.append(2)) is None
    assert built == [] and reads == []
    on = module.make_proposer(service, ledger=module.MemoryProposalLedger(), enabled=True)
    assert on is not None and built == [1]

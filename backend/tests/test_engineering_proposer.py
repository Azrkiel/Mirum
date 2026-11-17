"""Offline provider/transport fixtures; these do not qualify live AI accuracy."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

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


class FakeGenai:
    """Offline stand-in for ``google.genai.Client``. Records every constructor call and request."""
    def __init__(self, monkeypatch, behaviour=None):
        from google import genai
        self.clients, self.requests, self.events = [], [], []
        self.behaviour = behaviour or (lambda: self.reply())
        fake = self
        class Models:
            async def generate_content(self, *, model, contents, config):
                fake.requests.append(dict(model=model, contents=contents, config=config))
                fake.events.append('called')
                return await fake.behaviour()
        class Aio:
            models = Models()
            async def aclose(self):
                fake.events.append('closed')
        class Client:
            def __init__(self, **kwargs):
                fake.clients.append(kwargs)
                self.aio = Aio()
        monkeypatch.setattr(genai, 'Client', Client)

    async def reply(self, text=None, finish='STOP', parts=None):
        from google.genai import types
        if parts is None:
            parts = [types.Part(text=response() if text is None else text)]
        return types.GenerateContentResponse(
            candidates=[types.Candidate(finish_reason=types.FinishReason[finish], content=types.Content(role='model', parts=parts))],
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=20))


def test_sdk_schema_builds_offline_and_request_pins_every_limit(monkeypatch):
    from google.genai import types
    from engineering.proposer import GeminiProposalProvider, provider_schema
    fake = FakeGenai(monkeypatch)
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    provider = GeminiProposalProvider()
    result = asyncio.run(provider.generate('fixture', provider_schema(), provider.settings()))
    assert result.prompt_tokens == 10 and result.output_tokens == 20
    assert fake.events == ['called', 'closed']
    (kwargs,) = fake.clients
    assert kwargs['api_key'] == 'offline-placeholder' and kwargs['vertexai'] is False
    options = kwargs['http_options']
    assert options.timeout == 45000                       # milliseconds, never longer than the 45 s deadline
    assert options.retry_options.attempts == 1            # SDK retries disabled: exactly one attempt
    assert options.httpx_async_client is None and options.async_client_args is None
    (request,) = fake.requests
    assert request['model'] == 'gemini-4-hydrogen'
    assert request['contents'] == 'fixture'
    config = request['config']
    assert config.temperature == 0.4 and config.max_output_tokens == 4096 and config.candidate_count == 1
    assert config.response_mime_type == 'application/json'
    assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert not config.thinking_config.include_thoughts
    assert config.response_schema.type == types.Type.OBJECT
    assert config.response_schema.properties['state'].type == types.Type.STRING
    assert config.response_schema.properties['fields'].properties['mode'].nullable is True


def test_provider_schema_converts_to_the_current_sdk_type():
    from google.genai import types
    from engineering.proposer import provider_schema, _sdk_schema
    assert types.Schema(**_sdk_schema(provider_schema()))
    assert 'type_' not in json.dumps(_sdk_schema(provider_schema()))


def test_thinking_level_is_an_explicit_recorded_setting():
    import config
    from engineering.proposer import GeminiProposalProvider
    assert config.GEMINI_ENGINEERING_THINKING_LEVEL == 'low'
    assert GeminiProposalProvider().settings().thinking_level == 'low'
    assert GeminiProposalProvider().settings().sdk_version.startswith('google-genai/')


def test_hung_call_is_cancelled_at_the_deadline_and_the_client_closed(monkeypatch):
    from engineering import proposer as module
    async def hang():
        await asyncio.sleep(3600)
    fake = FakeGenai(monkeypatch, behaviour=hang)
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    monkeypatch.setattr(module, 'CALL_SECONDS', 0.05)
    provider = module.GeminiProposalProvider()
    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(provider.generate('fixture', module.provider_schema(), provider.settings()))
    assert time.monotonic() - started < 5
    assert fake.events == ['called', 'closed']


def test_deadline_constant_is_45_seconds_and_sdk_timeout_matches():
    from engineering import proposer as module
    assert module.CALL_SECONDS == 45


def test_outer_cancellation_closes_the_client(monkeypatch):
    from engineering import proposer as module
    async def hang():
        await asyncio.sleep(3600)
    fake = FakeGenai(monkeypatch, behaviour=hang)
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    provider = module.GeminiProposalProvider()
    async def run():
        task = asyncio.ensure_future(provider.generate('fixture', module.provider_schema(), provider.settings()))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert fake.events == ['called', 'closed']


def test_invalid_reply_triggers_exactly_one_repair_and_records_the_failure(monkeypatch):
    replies = ['{"state": "bogus"}', response()]
    fake = FakeGenai(monkeypatch)
    async def behaviour():
        return await fake.reply(text=replies.pop(0))
    fake.behaviour = behaviour
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    from engineering.proposer import GeminiProposalProvider, REPAIR_PROMPT
    _, draft, proposer, ledger = setup(GeminiProposalProvider())
    result = propose(proposer, draft)
    assert result.state == 'proposed'
    assert len(fake.requests) == 2 and fake.events == ['called', 'closed', 'called', 'closed']
    assert REPAIR_PROMPT not in fake.requests[0]['contents'] and fake.requests[1]['contents'].endswith(REPAIR_PROMPT)
    audit = ledger.read(draft.draft_id, 1)
    assert audit.calls == 2 and len(audit.validation_failures) == 1 and audit.validation_failures[0].attempt == 1
    assert audit.settings.thinking_level == 'low'


def test_a_second_invalid_reply_makes_no_third_call(monkeypatch):
    fake = FakeGenai(monkeypatch)
    async def behaviour():
        return await fake.reply(text='not json')
    fake.behaviour = behaviour
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    from engineering.proposer import GeminiProposalProvider
    _, draft, proposer, ledger = setup(GeminiProposalProvider())
    assert propose(proposer, draft).state == 'unavailable'
    assert len(fake.requests) == 2 and len(ledger.read(draft.draft_id, 1).validation_failures) == 2


def test_no_key_in_exceptions_or_logs(monkeypatch, caplog):
    key = 'AIzaSy-offline-secret-key-0123456789'
    async def leak():
        raise RuntimeError('request failed with x-goog-api-key: ' + key)
    FakeGenai(monkeypatch, behaviour=leak)
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', key)
    from engineering.proposer import GeminiProposalProvider, provider_schema
    provider = GeminiProposalProvider()
    with caplog.at_level('DEBUG'):
        with pytest.raises(Exception) as caught:
            asyncio.run(provider.generate('fixture', provider_schema(), provider.settings()))
        _, draft, proposer, ledger = setup(GeminiProposalProvider())
        result = propose(proposer, draft)
    rendered = repr(caught.value) + str(caught.value) + "".join(traceback.format_exception(caught.value)) + caplog.text
    rendered += result.model_dump_json() + ledger.read(draft.draft_id, 1).model_dump_json()
    assert key not in rendered and 'x-goog-api-key' not in rendered
    chain, seen = [caught.value], set()
    while chain:
        error = chain.pop()
        if id(error) in seen or error is None: continue
        seen.add(id(error))
        assert key not in str(error) + repr(error) + repr(error.args)
        chain += [error.__cause__, error.__context__]
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_missing_key_builds_no_client(monkeypatch):
    from engineering.proposer import GeminiProposalProvider, provider_schema
    fake = FakeGenai(monkeypatch)
    monkeypatch.delenv('GEMINI_API_KEY_HEAVY', raising=False)
    monkeypatch.setenv('GEMINI_API_KEY', 'must-not-be-used')
    monkeypatch.setenv('GOOGLE_API_KEY', 'must-not-be-used')
    provider = GeminiProposalProvider()
    assert not provider.available
    with pytest.raises(RuntimeError, match='credentials unavailable'):
        asyncio.run(provider.generate('fixture', provider_schema(), provider.settings()))
    assert fake.clients == []


def test_only_the_heavy_key_is_used(monkeypatch):
    from engineering.proposer import GeminiProposalProvider, provider_schema
    fake = FakeGenai(monkeypatch)
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'heavy-key')
    monkeypatch.setenv('GEMINI_API_KEY', 'other-key')
    monkeypatch.setenv('GOOGLE_API_KEY', 'other-key')
    provider = GeminiProposalProvider()
    asyncio.run(provider.generate('fixture', provider_schema(), provider.settings()))
    assert fake.clients[0]['api_key'] == 'heavy-key'


def test_checked_preparation_selects_36_flash_without_changing_legacy_models():
    import config
    from subagents.model_registry import ModelRegistry
    from engineering.proposer import GeminiProposalProvider
    registry = ModelRegistry.instance()
    legacy = {name: registry.get(name).model_id for name in ('clarifier', 'classifier', 'machinist')}
    settings = GeminiProposalProvider().settings()
    assert settings.model == 'gemini-4-hydrogen'
    assert settings.temperature == 0.4 and settings.max_tokens == 4096
    assert registry.get('clarifier').max_tokens == 1024
    assert config.GEMINI_MODEL_LITE == config.GEMINI_MODEL_HEAVY == 'gemini-3.5-flash'
    assert {name: registry.get(name).model_id for name in legacy} == legacy


@pytest.mark.parametrize('finish', ['MAX_TOKENS', 'SAFETY', 'OTHER'])
def test_gemini_truncated_candidate_stops_without_schema_repair(monkeypatch, finish):
    from engineering.proposer import GeminiProposalProvider
    fake = FakeGenai(monkeypatch)
    async def behaviour():
        return await fake.reply(text='private incomplete provider reply', finish=finish)
    fake.behaviour = behaviour
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    _, draft, proposer, ledger = setup(GeminiProposalProvider())
    result = propose(proposer, draft)
    assert result.state == 'unavailable'
    assert fake.events == ['called', 'closed'] and ledger.read(draft.draft_id, 1).calls == 1
    assert 'private incomplete' not in str(result) + ledger.read(draft.draft_id, 1).model_dump_json()


def test_gemini_candidate_without_text_fails_visibly(monkeypatch):
    from engineering.proposer import GeminiProposalProvider, provider_schema
    from google.genai import types
    fake = FakeGenai(monkeypatch)
    async def behaviour():
        return await fake.reply(parts=[types.Part(text='ok'), types.Part()])
    fake.behaviour = behaviour
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    provider = GeminiProposalProvider()
    with pytest.raises(RuntimeError, match='no JSON data'):
        asyncio.run(provider.generate('fixture', provider_schema(), provider.settings()))
    assert fake.events == ['called', 'closed']


def test_gemini_zero_candidates_fail_visibly(monkeypatch):
    from engineering.proposer import GeminiProposalProvider, provider_schema
    from google.genai import types
    fake = FakeGenai(monkeypatch)
    async def behaviour():
        return types.GenerateContentResponse(candidates=[])
    fake.behaviour = behaviour
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    provider = GeminiProposalProvider()
    with pytest.raises(RuntimeError, match='complete candidate'):
        asyncio.run(provider.generate('fixture', provider_schema(), provider.settings()))


def test_gemini_os_trust_opt_in_uses_installed_os_trust_without_global_override(monkeypatch):
    import os
    import ssl
    import httpx
    from engineering.proposer import GeminiProposalProvider, provider_schema
    import config
    monkeypatch.setattr(config, 'ENGINEERING_TLS_OS_TRUST', True)  # explicit local-diagnostic opt-in
    contexts = []
    real = ssl.create_default_context
    def tracking(*args, **kwargs):
        contexts.append(real(*args, **kwargs))
        return contexts[-1]
    seen = {}
    async def behaviour():
        seen['open_during_call'] = not fake.clients[0]['http_options'].httpx_async_client.is_closed
        raise RuntimeError('offline provider failure')
    fake = FakeGenai(monkeypatch, behaviour=behaviour)
    monkeypatch.delenv('SSL_CERT_FILE', raising=False)
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    monkeypatch.setattr(ssl, 'create_default_context', tracking)
    provider = GeminiProposalProvider()
    with pytest.raises(RuntimeError, match='offline provider failure'):
        asyncio.run(provider.generate('fixture', provider_schema(), provider.settings()))
    client = fake.clients[0]['http_options'].httpx_async_client
    assert isinstance(client, httpx.AsyncClient) and client.is_closed and seen['open_during_call']
    assert contexts and contexts[0].get_ca_certs(binary_form=True)
    assert 'SSL_CERT_FILE' not in os.environ and fake.events == ['called', 'closed']


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
assert not any(n in sys.modules for n in ['agents', 'google.generativeai', 'google.ai.generativelanguage_v1beta', 'google.genai', 'grpc', 'cadquery', 'OCP', 'numpy'])
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


@pytest.mark.parametrize('make,expected', [
    (lambda e: e.ServerError(503, {'error': {'code': 503, 'message': 'The model is overloaded', 'status': 'UNAVAILABLE'}}),
     dict(exception='ServerError', code='503')),
    (lambda e: e.ClientError(400, {'error': {'code': 400, 'message': 'API key not valid. Please pass a valid API key.', 'status': 'INVALID_ARGUMENT'}}),
     dict(exception='ClientError', code='400', credential_rejected=True)),
    (lambda e: e.ClientError(404, {'error': {'code': 404, 'message': 'models/x is not found for API version', 'status': 'NOT_FOUND'}}),
     dict(exception='ClientError', code='404', model_unavailable=True)),
    (lambda e: e.ClientError(400, {'error': {'code': 400, 'message': 'response_schema is invalid', 'status': 'INVALID_ARGUMENT'}}),
     dict(exception='ClientError', code='400', schema_rejected=True)),
    (lambda e: RuntimeError('[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed'),
     dict(exception='RuntimeError', code=None, tls_certificate_failure=True)),
    (lambda e: RuntimeError('AI preparation did not return a complete candidate'),
     dict(exception='RuntimeError', code=None, incomplete_response=True)),
])
def test_observed_provider_classifies_google_genai_errors_safely(monkeypatch, make, expected):
    from google.genai import errors
    from scripts.checked_engineering_live_eval import ObservedProvider
    async def failing():
        raise make(errors)
    FakeGenai(monkeypatch, behaviour=failing)
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'offline-placeholder')
    from engineering.proposer import provider_schema
    provider = ObservedProvider()
    with pytest.raises(Exception):
        asyncio.run(provider.generate('fixture', provider_schema(), provider.settings()))
    (entry,) = provider.failures
    flags = dict(tls_certificate_failure=False, credential_rejected=False, model_unavailable=False,
                 schema_rejected=False, incomplete_response=False)
    flags.update(expected)
    assert entry == flags
    assert 'overloaded' not in json.dumps(entry) and 'offline-placeholder' not in json.dumps(entry)


def test_live_eval_identity_lists_google_genai_not_the_old_sdk():
    source = (Path(__file__).parents[2]/'scripts'/'checked_engineering_live_eval.py').read_text()
    assert "'google-genai'" in source and "'google-ai-generativelanguage'" not in source and "'grpcio'" not in source

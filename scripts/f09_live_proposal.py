"""F09 AI proposal route harness: rehearsal (provider-free) or live (real Gemini, protected environment).

Route under test, exactly as the workspace drives it: typed POST /drafts mode 'ai' capability beam ->
frontend.program_workspace._source_from_beam -> POST /mel/plan -> stage on a program with the
X-Mirum-Require-Requirements guard and source id typed-ai-proposal.mir -> review -> run.

Only --mode differs between rehearsal and live. Rehearsal injects the typed proposer fixture at the provider
interface (ProposalProvider.generate), the very place the live run uses the real Gemini provider, and
refuses any credential file and any network connection. Live needs a credential file, a distinct authorized
run identity and an immutable candidate. Provider calls are counted from the proposal ledger around every step.

Evidence JSON never contains a prompt, a raw reply, a key or the ledger: only hashes, counts and verdicts.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'backend' / 'tests' / 'fixtures' / 'program'
F09 = FIXTURES / 'f09'
P03 = FIXTURES / 'p03'
V1 = '/engineering/v1'
HEAD_SOURCE_ID = 'examples/beam.mir'
AI_SOURCE_ID = 'typed-ai-proposal.mir'
CAPABILITY_ID = 'rectangular_cantilever'
CEILING = 6
AI_REQUESTS = 3
POLL_SECONDS = 540.0
EVIDENCE_NAME = 'f09-live.json'

PROMPTS = {
    'equivalent': (
        'Prepare the reviewed reference cantilever beam: 200 mm long and 10 mm wide, dimensional tolerance '
        '0.01 mm, 50 N tip force, maximum tip deflection 1 mm. Material: Young\'s modulus 70000 MPa, allowable '
        'stress 120 MPa, illustrative properties with the reference "Illustrative user-provided properties; '
        'not certified stock values". Assume an ideal clamped end with force and linear elastic behavior. '
        'Use bounded thickness mode with candidate heights 5 mm, 10 mm and 15 mm.'),
    'length_201': (
        'Same reference cantilever beam as before (10 mm wide, 0.01 mm tolerance, 50 N tip force, 1 mm maximum '
        'tip deflection, 70000 MPa modulus, 120 MPa allowable stress, illustrative material, ideal clamped end '
        'with linear elastic behavior, bounded thickness mode, candidate heights 5 mm, 10 mm and 15 mm), but '
        'change the length to 201 mm. Keep everything else the same.'),
    'candidate_20': (
        'Same reference cantilever beam as before: 200 mm long, 10 mm wide, 0.01 mm tolerance, 50 N tip force, '
        '1 mm maximum tip deflection, 70000 MPa modulus, 120 MPa allowable stress, illustrative material, '
        'ideal clamped end with linear elastic behavior, bounded thickness mode. Change only the candidate '
        'choices: try a single candidate height of 20 mm.'),
}
REPLY_FILES = {'equivalent': 'reply_equivalent.json', 'length_201': 'reply_length_201.json',
               'candidate_20': 'reply_candidate_20.json'}


class Blocked(Exception):
    """Argument or environment problem found before any provider call."""


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode('utf-8')).hexdigest()


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('rehearsal', 'live'), required=True)
    parser.add_argument('--evidence-dir', required=True)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--credential-file')
    parser.add_argument('--run-id', default='rehearsal')
    parser.add_argument('--authorization', default='none')
    parser.add_argument('--source-sha', default='none')
    parser.add_argument('--image-id', default='none')
    parser.add_argument('--max-provider-calls', type=int, default=6)
    return parser.parse_args(argv)


def validate(args):
    if not 1 <= args.max_provider_calls <= CEILING:
        raise Blocked('max-provider-calls must be between 1 and %d' % CEILING)
    if args.mode == 'rehearsal':
        if args.credential_file:
            raise Blocked('rehearsal refuses a credential file')
    else:
        if not (args.credential_file and Path(args.credential_file).is_file()):
            raise Blocked('live mode requires an existing credential file')
        if (not all(isinstance(v, str) and v.strip() and v != 'none' for v in (args.run_id, args.authorization))
                or re.fullmatch(r'[0-9a-f]{40}', args.source_sha) is None
                or re.fullmatch(r'sha256:[0-9a-f]{64}', args.image_id) is None):
            raise Blocked('live mode requires a distinct authorized run identity and an immutable candidate')
    state = Path(args.state_dir)
    if state.exists() and (not state.is_dir() or any(state.iterdir())):
        raise Blocked('state dir must be new or empty; a reserved state root is never reused')


# ------------------------------------------------------------------------------------------ environment

def load_workspace_helper():
    """Import _source_from_beam from frontend/program_workspace.py (never copied).

    The backend image may lack streamlit or the frontend's helper modules; only those absent modules are
    replaced by inert stand-ins, and which ones is recorded in the evidence."""
    stubbed = []
    frontend = str(ROOT / 'frontend')
    if frontend not in sys.path:
        sys.path.insert(0, frontend)
    for _ in range(4):
        try:
            spec = importlib.util.spec_from_file_location('f09_program_workspace', ROOT / 'frontend' / 'program_workspace.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module._source_from_beam, stubbed
        except ImportError as error:
            name = (error.name or '').split('.')[0]
            if not name or name in stubbed or name not in ('streamlit', 'requests', 'engineering_ui'):
                raise
            stub = types.ModuleType(name)
            if name == 'engineering_ui':
                stub.APIError = type('APIError', (Exception,), {})
            sys.modules[name] = stub
            stubbed.append(name)
    raise ImportError('program_workspace helper unavailable')


def guard_network():
    """Rehearsal: any IP connection or name lookup is refused and counted."""
    import socket
    attempts = []
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def refuse(self, *args):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            attempts.append(True)
            raise OSError('network disabled in rehearsal')
        return real_connect(self, *args)

    def refuse_ex(self, *args):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            attempts.append(True)
            return 1
        return real_connect_ex(self, *args)

    def refuse_lookup(*args, **kwargs):
        attempts.append(True)
        raise OSError('network disabled in rehearsal')
    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = refuse, refuse_ex, refuse_lookup
    return attempts


class FixtureProvider:
    """Typed proposer fixture at the ProposalProvider interface; replies chosen by the request in the prompt."""
    available = True

    def __init__(self):
        self.replies = {key: (F09 / name).read_text(encoding='utf-8') for key, name in REPLY_FILES.items()}
        self.calls = 0

    def settings(self):
        from engineering.proposer import ProviderSettings
        return ProviderSettings(provider='fixture', sdk_version='fixture/1', model='f09-rehearsal-fixture',
                                temperature=0.0, max_tokens=4096, thinking_level=None)

    async def generate(self, prompt, schema, settings):
        from engineering.proposer import ProviderReply
        self.calls += 1
        for key, text in PROMPTS.items():
            if json.dumps(text, ensure_ascii=False) in prompt:
                return ProviderReply(text=self.replies[key], prompt_tokens=0, output_tokens=0)
        raise RuntimeError('fixture has no reply for this prompt')


# ------------------------------------------------------------------------------------------------ steps

class World:
    def __init__(self, client, runtime, ledger, source_from_beam, cap):
        self.c, self.runtime, self.ledger, self.convert, self.cap = client, runtime, ledger, source_from_beam, cap
        self.head = None          # {'pid', 'rid', 'fixed'} after step 3
        self.identity = {}        # hashes carried between steps

    def req(self, method, path, **kw):
        response = self.c.request(method, V1 + path, **kw)
        try:
            body = response.json()
        except ValueError:
            body = None
        return types.SimpleNamespace(status=response.status_code, json=body, raw=response.content)

    def calls(self):
        return len(self.ledger.recent_calls())

    def history(self):
        got = self.req('GET', '/programs/%s/history' % self.head['pid'])
        assert got.status == 200
        return got.json

    def poll(self, rid):
        deadline = time.monotonic() + POLL_SECONDS
        while True:
            got = self.req('GET', '/revisions/' + rid)
            assert got.status == 200
            if got.json['revision']['result_kind'] != 'pending':
                return got.json['revision']
            assert time.monotonic() < deadline, 'revision did not reach a terminal state'
            time.sleep(1.0)

    def stage(self, pid, source, *, guard=None, parent=None, source_id=None):
        headers = {'Content-Type': 'application/vnd.mirum.mel'}
        if source_id:
            headers['X-Mirum-Source-Id'] = source_id
        if parent:
            headers['X-Mirum-Expected-Parent'] = parent
        if guard:
            headers['X-Mirum-Require-Requirements'] = guard
        return self.req('POST', '/programs/%s/revisions' % pid, content=source, headers=headers)

    def review(self, staged):
        revision = staged.json['revision']
        return self.req('POST', '/revisions/%s/review' % revision['revision_id'], json={
            'brief_sha256': revision['brief_sha256'], 'acknowledged_fields': staged.json['review_fields'],
            'reviewed_by': 'f09-harness'})

    def ai_draft(self, key):
        """The typed draft route, as the workspace calls it. Returns (status, draft view or None)."""
        got = self.req('POST', '/drafts', json={'mode': 'ai', 'capability': CAPABILITY_ID, 'input': {},
                                                'prompt': PROMPTS[key]})
        return got.status, got.json

    def ai_source(self, key):
        calls_before = self.calls()
        status, prepared = self.ai_draft(key)
        proposal_calls = self.calls() - calls_before
        if status != 201 and status != 200:
            raise AssertionError('typed draft route answered %s' % status)
        content = prepared['draft']['content']
        if prepared['issues'] or content is None:
            raise AssertionError('proposal needs clarification')
        source = self.convert(content).encode('utf-8')
        planned = self.req('POST', '/mel/plan', content=source, headers={'Content-Type': 'application/vnd.mirum.mel'})
        if planned.status != 200:
            raise AssertionError('converted source failed the compiler plan route')
        origins = prepared['draft']['provenance']['field_origins']
        prompt_sha = sha(PROMPTS[key])
        link = dict(draft_input_mode=prepared['draft']['provenance']['input_mode'],
                    ai_proposed_origin_count=sum(o['origin'] == 'ai_proposed' for o in origins),
                    origin_count=len(origins), prompt_sha256=prompt_sha,
                    prompt_sha256_in_all_origins=bool(origins) and all(prompt_sha in o['reference'] for o in origins),
                    draft_brief_sha256=prepared['brief_sha256'], converted_source_sha256=sha(source),
                    ledger_calls_for_proposal=proposal_calls)
        link['linked'] = (link['draft_input_mode'] == 'ai' and link['ai_proposed_origin_count'] == len(origins) > 0
                          and link['prompt_sha256_in_all_origins'] and proposal_calls >= 1)
        return prepared, source, planned.json, link


def compiled(source, source_id):
    from engineering.language.semantic import compile_source
    return compile_source(source, source_id=source_id)


def step_form(w):
    from engineering.contracts import BriefContent
    from engineering.program.beam_bridge import program_from_brief_content
    from engineering.program.hashing import semantic_sha256, source_sha256
    from engineering.program.ir import ProgramProvenance
    payload = json.loads((P03 / 'form.json').read_text(encoding='utf-8'))
    got = w.req('POST', '/drafts', json={'mode': 'form', 'capability': CAPABILITY_ID, 'input': payload})
    assert got.status in (200, 201), got.status
    view = got.json
    content = BriefContent.model_validate_json(json.dumps(view['draft']['content']))
    program = program_from_brief_content(content, provenance=ProgramProvenance(
        kind='form', source_sha256=source_sha256(payload), source_id='form.json'))
    w.identity['form'] = dict(
        semantic=semantic_sha256(program), brief=view['brief_sha256'], fixed=view['fixed_requirements_sha256'],
        candidates=dict(mode=content.mode, heights_mm=list(content.candidate_heights_mm)),
        source=source_sha256(payload))
    kind = view['draft']['provenance']['input_mode']
    return dict(passed=not view['issues'] and kind == 'form' and view['brief_sha256'] == content.brief_sha256,
                semantic_sha256=w.identity['form']['semantic'], brief_sha256=view['brief_sha256'],
                fixed_requirements_sha256=view['fixed_requirements_sha256'],
                candidates=w.identity['form']['candidates'], provenance_kind=kind)


def mel_identity(source, planned, source_id):
    c = compiled(source, source_id)
    return dict(semantic=c.semantic_sha256, brief=c.brief_sha256,
                fixed=c.brief_content.fixed_requirements_sha256,
                candidates=dict(mode=c.brief_content.mode, heights_mm=list(c.brief_content.candidate_heights_mm)),
                source=c.source_sha256, kind=c.ir.provenance.kind, plan_semantic=planned['semantic_sha256'],
                candidate_count=planned['candidate_count'])


def step_mel(w):
    source = (P03 / 'beam.mir').read_bytes()
    planned = w.req('POST', '/mel/plan', content=source, headers={
        'Content-Type': 'application/vnd.mirum.mel', 'X-Mirum-Source-Id': HEAD_SOURCE_ID})
    assert planned.status == 200, planned.status
    ident = mel_identity(source, planned.json, HEAD_SOURCE_ID)
    w.identity['mel'] = ident
    form = w.identity['form']
    same = (ident['semantic'] == ident['plan_semantic'] == form['semantic'] and ident['brief'] == form['brief']
            and ident['fixed'] == form['fixed'] and ident['candidates'] == form['candidates'])
    return dict(passed=same and ident['kind'] == 'language' and ident['candidate_count'] == 3,
                semantic_sha256=ident['semantic'], brief_sha256=ident['brief'], source_sha256=ident['source'],
                candidates=ident['candidates'], candidate_count=ident['candidate_count'],
                provenance_kind=ident['kind'], equals_form=same)


def published_facts(w, rid):
    inspected = w.req('GET', '/revisions/%s/evidence' % rid)
    spec = w.req('GET', '/revisions/%s/artifacts/spec' % rid)
    assert inspected.status == 200 and spec.status == 200
    evidence = inspected.json['evidence']
    run = evidence.get('run') or {}
    return dict(verdict=evidence.get('verdict'), status=evidence.get('status'),
                attempt_outcomes=[a['outcome'] for a in sorted(run.get('attempts', ()), key=lambda a: a['ordinal'])],
                height_mm=json.loads(spec.raw)['height_mm'], manifest_sha256=evidence.get('manifest_sha256'))


def step_head(w):
    source = (P03 / 'beam.mir').read_bytes()
    created = w.req('POST', '/programs')
    assert created.status == 201
    pid = created.json['program_id']
    w.head = dict(pid=pid)
    staged = w.stage(pid, source, source_id=HEAD_SOURCE_ID)
    assert staged.status == 201, staged.status
    reviewed = w.review(staged)
    assert reviewed.status == 200, reviewed.status
    rid = staged.json['revision']['revision_id']
    ran = w.req('POST', '/revisions/%s/run' % rid)
    assert ran.status == 200
    final = w.poll(rid)
    facts = published_facts(w, rid)
    w.head.update(rid=rid, fixed=final['fixed_requirements_sha256'])
    ok = (final['state'] == 'published' and final['is_current'] and facts['verdict'] == 'verified'
          and facts['height_mm'] == 15 and facts['attempt_outcomes'] == ['rejected', 'rejected', 'accepted']
          and final['brief_sha256'] == w.identity['mel']['brief']
          and final['fixed_requirements_sha256'] == w.identity['mel']['fixed'])
    return dict(passed=bool(ok), state=final['state'], selected_height_mm=facts['height_mm'],
                evidence_verdict=facts['verdict'], attempt_outcomes=facts['attempt_outcomes'],
                head_fixed_requirements_sha256=final['fixed_requirements_sha256'],
                brief_sha256=final['brief_sha256'], revision_id=rid)


def step_equivalent(w):
    prepared, source, planned, link = w.ai_source('equivalent')
    ident = mel_identity(source, planned, AI_SOURCE_ID)
    draft = prepared['draft']
    kind = draft['provenance']['input_mode']
    prompt_sha = sha(PROMPTS['equivalent'])
    in_origins = bool(draft['provenance']['field_origins']) and all(
        o['origin'] == 'ai_proposed' and prompt_sha in o['reference'] for o in draft['provenance']['field_origins'])
    mel, form = w.identity['mel'], w.identity['form']
    same = all(ident[k] == mel[k] == form[k] for k in ('semantic', 'brief', 'fixed')) and (
        ident['candidates'] == mel['candidates'] == form['candidates'])
    distinct = len({ident['source'], mel['source'], form['source']}) == 3
    w.identity['ai_equivalent_source'] = ident['source']
    return dict(link=link, passed=bool(link['linked'] and same and distinct and kind == 'ai' and in_origins and prepared['brief_sha256'] == form['brief']),
                semantic_sha256=ident['semantic'], brief_sha256=ident['brief'], candidates=ident['candidates'],
                equals_form_and_mel=same, draft_provenance_kind=kind, prompt_sha256=prompt_sha,
                prompt_sha256_in_origins=in_origins, ai_source_sha256=ident['source'], source_hashes_distinct=distinct)


def step_guard(w):
    head = w.head
    before = w.history()
    prepared, source, _, link = w.ai_source('length_201')
    ident = mel_identity(source, {'semantic_sha256': '', 'candidate_count': 0}, AI_SOURCE_ID)
    if ident['fixed'] == head['fixed']:
        return dict(link=link, passed=False, status='not_exercised', reason='provider did not change the length',
                    ai_source_sha256=ident['source'])
    refused = w.stage(head['pid'], source, guard=head['fixed'], parent=head['rid'], source_id=AI_SOURCE_ID)
    after = w.history()
    unchanged = (len(after['revisions']) == len(before['revisions']) and after['head_revision_id'] == head['rid']
                 == before['head_revision_id'])
    code = (refused.json or {}).get('code')
    return dict(link=link, passed=bool(link['linked'] and refused.status == 409 and code == 'requirements_changed' and unchanged),
                status='exercised', http_status=refused.status, error_code=code,
                revisions_before=len(before['revisions']), revisions_after=len(after['revisions']),
                head_unchanged=unchanged, ai_source_sha256=ident['source'], proposed_fixed_requirements_sha256=ident['fixed'])


def step_candidate(w):
    head = w.head
    before = w.history()
    prepared, source, _, link = w.ai_source('candidate_20')
    ident = mel_identity(source, {'semantic_sha256': '', 'candidate_count': 0}, AI_SOURCE_ID)
    staged = w.stage(head['pid'], source, guard=head['fixed'], parent=head['rid'], source_id=AI_SOURCE_ID)
    assert staged.status == 201, 'guarded stage answered %s' % staged.status
    rid = staged.json['revision']['revision_id']
    head_after_stage = w.history()['head_revision_id']
    early = w.req('POST', '/revisions/%s/run' % rid)
    still_staged = w.req('GET', '/revisions/' + rid).json['revision']['state'] == 'staged'
    reviewed = w.review(staged)
    assert reviewed.status == 200
    ran = w.req('POST', '/revisions/%s/run' % rid)
    assert ran.status == 200
    final = w.poll(rid)
    facts = published_facts(w, rid)
    ir = json.loads(w.req('GET', '/revisions/%s/artifacts/program-ir' % rid).raw)
    after = w.history()
    link['revision_source_sha256'] = final['source_sha256']
    link['revision_source_equals_converted'] = final['source_sha256'] == link['converted_source_sha256']
    ok = (link['linked'] and link['revision_source_equals_converted']
          and staged.json['revision']['source_sha256'] == link['converted_source_sha256']
          and staged.json['revision']['fixed_requirements_sha256'] == head['fixed']
          and early.status >= 400 and still_staged and head_after_stage == head['rid']
          and final['state'] == 'published' and final['is_current'] and facts['verdict'] == 'verified'
          and facts['height_mm'] == 20 and ir['provenance']['source_id'] == AI_SOURCE_ID
          and after['head_revision_id'] == rid and before['head_revision_id'] == head['rid'])
    return dict(link=link, passed=bool(ok), staged=True, fixed_requirements_unchanged=staged.json['revision']['fixed_requirements_sha256'] == head['fixed'],
                run_before_review_http_status=early.status, run_before_review_code=(early.json or {}).get('code'),
                run_before_review_left_staged=still_staged, head_unchanged_until_publish=head_after_stage == head['rid'],
                state=final['state'], evidence_verdict=facts['verdict'], selected_height_mm=facts['height_mm'],
                attempt_outcomes=facts['attempt_outcomes'], source_id=ir['provenance']['source_id'],
                ai_source_sha256=ident['source'], source_sha256=final['source_sha256'], revision_id=rid,
                new_head=after['head_revision_id'] == rid)


STEPS = (('S1', 'form', step_form), ('S2', 'mel', step_mel), ('S3', 'head', step_head),
         ('S4', 'ai_equivalent', step_equivalent), ('S5', 'guard_refusal', step_guard),
         ('S6', 'allowed_candidate', step_candidate))
AI_STEPS = {'S4', 'S5', 'S6'}


def write_evidence(directory, evidence):
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / (EVIDENCE_NAME + '.tmp')
    temporary.write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temporary.replace(directory / EVIDENCE_NAME)


def execute(args, evidence):
    state = Path(args.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    work = state / 'work'
    work.mkdir()
    rehearsal = args.mode == 'rehearsal'
    network = guard_network() if rehearsal else []
    sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
    os.environ.update(MIRUM_ENGINEERING_DATA_DIR=str(state / 'engineering'), MIRUM_WORK_DIR=str(work),
                      MIRUM_ENGINEERING_PROGRAMS='1', MIRUM_ENGINEERING_AI_DRAFTS='1',
                      MIRUM_ENGINEERING_AI_DAILY_CALLS=str(args.max_provider_calls),
                      MIRUM_ENGINEERING_AI_REQUESTS_PER_CLIENT_DAY=str(AI_REQUESTS))
    os.environ.pop('MIRUM_ACCESS_TOKEN', None)
    os.environ.pop('MIRUM_ENGINEERING_REQUIRE_VOLUME', None)
    if rehearsal:
        os.environ['GEMINI_API_KEY_HEAVY'] = 'offline-fixture-never-sent'
        os.environ.pop('GEMINI_API_KEY_PRO', None)
    else:
        from scripts.checked_engineering_live_eval import load_credential
        evidence['credential_variable'] = load_credential(args.credential_file)
        if not evidence['credential_variable']:
            raise Blocked('credential file holds no usable Gemini key variable')
    convert, stubbed = load_workspace_helper()
    evidence['workspace_import_stubs'] = stubbed
    import importlib
    import config
    config.ENGINEERING_AI_DRAFTS, config.ENGINEERING_AI_DAILY_CALLS = True, args.max_provider_calls
    main = importlib.import_module('main')
    main.limiter.enabled = False
    from fastapi.testclient import TestClient
    from engineering.store import SQLiteProposalLedger
    with TestClient(main.app) as client:
        main.app.state.limiter.reset()
        runtime = main.app.state.engineering
        assert main.app.state.programs is not None, 'program routes are not enabled'
        assert runtime.ai_enabled and runtime.proposer is not None, 'AI drafts are not enabled'
        fixture = None
        if rehearsal:
            fixture = FixtureProvider()
            runtime.proposer.provider = fixture
        else:
            from scripts.checked_engineering_live_eval import ObservedProvider
            observed = runtime.proposer.provider = ObservedProvider()
            if not observed.available:
                raise Blocked('provider unavailable; no live calls made')
        evidence['provider'] = runtime.proposer.provider.settings().model_dump(mode='json')
        w = World(client, runtime, SQLiteProposalLedger(runtime.store), convert, args.max_provider_calls)
        failed = False
        for step_id, name, function in STEPS:
            record = dict(step=step_id, name=name, passed=False)
            evidence['steps'].append(record)
            if failed:
                record['status'] = 'not_run'
                continue
            start_calls = w.calls()
            started = time.monotonic()
            try:
                record.update(function(w))
            except Exception as error:  # noqa: BLE001 - class name only; messages can echo model output
                record.update(passed=False, error_type=type(error).__name__)
            record['provider_calls'] = w.calls() - start_calls
            record['seconds'] = round(time.monotonic() - started, 3)
            evidence['provider_calls'] = w.calls()
            if step_id not in AI_STEPS and record['provider_calls']:
                record['passed'] = False
            if evidence['provider_calls'] > args.max_provider_calls:
                record['passed'] = False
                record['error_type'] = 'call_cap_exceeded'
            failed = not record['passed']
            write_evidence(Path(args.evidence_dir), evidence)
        evidence['provider_calls'] = w.calls()
        if fixture is not None:
            evidence['fixture_calls'] = fixture.calls
            evidence['fixture_calls_match_ledger'] = fixture.calls == evidence['provider_calls']
        if not rehearsal:
            evidence['provider_failures'] = list(observed.failures)
    evidence['network_attempts'] = len(network)
    ok = (all(s['passed'] for s in evidence['steps']) and len(evidence['steps']) == len(STEPS)
          and evidence['provider_calls'] <= args.max_provider_calls and not network
          and (not rehearsal or evidence.get('fixture_calls_match_ledger')))
    evidence['verdict'] = 'passed' if ok else 'failed'


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    evidence = dict(mode=args.mode, run_id=args.run_id, authorization=args.authorization, source_sha=args.source_sha,
                    image_id=args.image_id, max_provider_calls=args.max_provider_calls, provider_calls=0,
                    steps=[], verdict='blocked')
    if (Path(args.evidence_dir) / EVIDENCE_NAME).exists():
        print(json.dumps(dict(mode=args.mode, verdict='blocked', blocker='evidence file already exists; never overwritten')))
        return 2
    try:
        validate(args)
        execute(args, evidence)
    except Blocked as blocked:
        evidence['blocker'] = str(blocked)
    except Exception as error:  # noqa: BLE001 - class name only
        evidence['verdict'] = 'failed'
        evidence['error_type'] = type(error).__name__
    write_evidence(Path(args.evidence_dir), evidence)
    print(json.dumps({k: evidence.get(k) for k in ('mode', 'verdict', 'provider_calls', 'blocker')}))
    return 0 if evidence['verdict'] == 'passed' else 2


if __name__ == '__main__':
    sys.exit(main())

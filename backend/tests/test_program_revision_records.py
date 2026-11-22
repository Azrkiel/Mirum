"""F06 task 1: pure revision records, transition table, refusals, versions and schemas."""
from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from engineering.contracts import ArtifactIdentity, BundleIdentity
from engineering.language.semantic import compile_source
from engineering.program import revisions as rv
from engineering.program.hashing import execution_lock_v0_2_sha256
from engineering.program.ir import execution_lock_v0_2
from engineering.program.kernel import NativeVersion, ProbeResult, backend_lock, make_description
from engineering.program.revisions import (
    JOURNAL_EVENTS, REFUSAL_REASONS, REVISION_KINDS, REVISION_STATES, TERMINAL_STATES, TRANSITIONS,
    JournalEntry, ProgramRevision, RevisionPayload, RevisionRefusal, RevisionView, can_transition,
)
from engineering.program.schema_export import SCHEMA_DIR, SCHEMA_MODELS, check
from engineering.program.versions import (
    PROGRAM_JOURNAL_VERSION, PROGRAM_REVISION_VERSION, REVISION_DIFF_VERSION,
)

ROOT = Path(__file__).resolve().parents[2]
P03 = Path(__file__).resolve().parent / 'fixtures' / 'program' / 'p03'
NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
H = 'a' * 64
RID = '1' * 32
PID = '2' * 32
OTHER = '3' * 32
SRC_CAP = 65536
JSON_CAP = 262144


@pytest.fixture(scope='module')
def compiled():
    return compile_source((P03 / 'beam.mir').read_bytes())


@pytest.fixture(scope='module')
def lock(compiled):
    desc = make_description('occt', mode='native', adapter_version='adapter_1', tolerances=())
    probe = ProbeResult(backend='occt', mode='native', available=True,
                        native_versions=(NativeVersion(name='cadquery', version='2.6.1'),
                                         NativeVersion(name='OCP', version='7.8.1')))
    return execution_lock_v0_2(compiled.ir, backend_lock(desc, probe))


@pytest.fixture(scope='module')
def bundle():
    names = ('brief.json', 'selected_spec.json', 'model.py', 'model.step', 'preview.glb',
             'attempts.json', 'report.json', 'report.md')
    return BundleIdentity(payloads=tuple(ArtifactIdentity(path=n, sha256=H, bytes=10) for n in names),
                          manifest_sha256=H, manifest_bytes=10, zip_sha256=H, zip_bytes=10,
                          stored_bytes=200)


def rev_data(lock, _bundle, /, **over):
    bundle = _bundle
    data = dict(
        revision_id=RID, program_id=PID, parent=None, kind='edit', derived_from=None,
        evidence_revision_id=RID, state='published', reason=None, source_sha256=H,
        semantic_sha256=H, brief_sha256=H, fixed_requirements_sha256=H,
        lock_sha256=execution_lock_v0_2_sha256(lock), lock=lock, draft_id=OTHER, draft_revision=1,
        brief_id=OTHER, job_id=OTHER, bundle=bundle, published_seq=1, created_at=NOW, updated_at=NOW)
    data.update(over)
    return data


def make_rev(lock, _bundle, /, **over):
    return ProgramRevision(**rev_data(lock, _bundle, **over))


# ---- versions / schemas ----

def test_version_constants():
    assert PROGRAM_REVISION_VERSION == 'mirum_program_revision_v0_1'
    assert PROGRAM_JOURNAL_VERSION == 'mirum_program_journal_v0_1'
    assert REVISION_DIFF_VERSION == 'mirum_revision_diff_v0_1'


def test_schema_models_registered_and_files_in_sync():
    assert SCHEMA_MODELS['mirum_program_revision_v0_1.schema.json'] is ProgramRevision
    assert SCHEMA_MODELS['mirum_program_journal_v0_1.schema.json'] is JournalEntry
    for name in ('mirum_program_revision_v0_1.schema.json', 'mirum_program_journal_v0_1.schema.json'):
        assert (SCHEMA_DIR / name).is_file()
    assert check() == []


# ---- transition table ----

D5 = {
    'staged': {'running', 'cancelled', 'failed', 'conflict'},
    'running': {'published', 'rejected', 'failed', 'cancelled', 'conflict'},
    'published': set(), 'rejected': set(), 'failed': set(), 'cancelled': set(), 'conflict': set(),
}


def test_constants():
    assert REVISION_KINDS == ('edit', 'restore', 'rebuild')
    assert REVISION_STATES == ('staged', 'running', 'published', 'rejected', 'failed', 'cancelled',
                               'conflict')
    assert TERMINAL_STATES == ('published', 'rejected', 'failed', 'cancelled', 'conflict')
    assert set(TRANSITIONS) == set(REVISION_STATES)
    assert all(isinstance(v, frozenset) for v in TRANSITIONS.values())


@pytest.mark.parametrize('src,dst', list(itertools.product(REVISION_STATES, REVISION_STATES)))
def test_every_transition_pair(src, dst):
    assert can_transition(src, dst) is (dst in D5[src])


def test_can_transition_unknown_is_false():
    assert can_transition('bogus', 'running') is False
    assert can_transition('staged', 'bogus') is False
    assert can_transition('', '') is False


def test_terminal_states_have_no_exits():
    for s in TERMINAL_STATES:
        assert TRANSITIONS[s] == frozenset()


def test_journal_events_allowlist():
    assert JOURNAL_EVENTS == ('staged', 'admit_intent', 'linked', 'cancel_requested', 'published',
                              'rejected', 'failed', 'cancelled', 'conflict', 'restored', 'compacted',
                              'recovered')


# ---- refusals ----

def test_refusal_reasons_exact():
    assert REFUSAL_REASONS == ('stale_parent', 'not_restorable', 'already_current', 'history_expired',
                               'artifacts_expired', 'artifacts_missing', 'artifacts_altered',
                               'record_altered', 'review_expired', 'capacity')


@pytest.mark.parametrize('reason', REFUSAL_REASONS)
def test_refusal_valid(reason):
    r = RevisionRefusal((reason,), 'why')
    assert r.reasons == (reason,) and r.detail == 'why'


def test_refusal_list_normalised_and_frozen():
    r = RevisionRefusal(['stale_parent', 'capacity'])
    assert r.reasons == ('stale_parent', 'capacity') and r.detail is None
    with pytest.raises(Exception):
        r.reasons = ()  # type: ignore[misc]


@pytest.mark.parametrize('bad', [(), ('nope',), ('stale_parent', 'nope'), ('',)])
def test_refusal_fails_closed(bad):
    with pytest.raises(ValueError):
        RevisionRefusal(bad)


def test_refusal_detail_rules():
    RevisionRefusal(('capacity',), 'x' * 240)
    for bad in ('x' * 241, 'caf' + chr(0xE9), 5):
        with pytest.raises(ValueError):
            RevisionRefusal(('capacity',), bad)


# ---- ProgramRevision ----

def test_revision_roundtrip(lock, bundle):
    r = make_rev(lock, bundle)
    again = ProgramRevision.model_validate_json(r.model_dump_json())
    assert again == r
    assert again.schema_version == PROGRAM_REVISION_VERSION
    assert again.model_dump_json() == r.model_dump_json()


def test_revision_closed_and_frozen(lock, bundle):
    with pytest.raises(ValidationError):
        ProgramRevision(**rev_data(lock, bundle), extra_field=1)
    r = make_rev(lock, bundle)
    with pytest.raises(ValidationError):
        r.state = 'failed'  # type: ignore[misc]
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, schema_version='mirum_program_revision_v9')


@pytest.mark.parametrize('field', ['revision_id', 'program_id', 'parent', 'derived_from', 'draft_id',
                                   'brief_id', 'job_id'])
@pytest.mark.parametrize('bad', ['1' * 31, '1' * 33, 'A' * 32, '1' * 31 + '\n', 'g' * 32, ''])
def test_revision_id_fields_32_lower_hex(lock, bundle, field, bad):
    extra = {}
    if field == 'derived_from':
        extra = dict(kind='rebuild', parent=OTHER)
    over = {field: bad, **extra}
    if field == 'revision_id':
        over['evidence_revision_id'] = bad
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, **over)


@pytest.mark.parametrize('field', ['source_sha256', 'semantic_sha256', 'brief_sha256',
                                   'fixed_requirements_sha256', 'lock_sha256'])
@pytest.mark.parametrize('bad', ['a' * 63, 'a' * 65, 'A' * 64, 'a' * 63 + 'g', 'a' * 63 + '\n'])
def test_revision_hash_fields_64_lower_hex(lock, bundle, field, bad):
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, **{field: bad})
    make_rev(lock, bundle, **{field: 'b' * 64})


def test_revision_reason_limit_and_ascii(lock, bundle):
    make_rev(lock, bundle, reason='x' * 240)
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, reason='x' * 241)
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, reason='caf' + chr(0xE9))


def test_revision_naive_datetime_and_order(lock, bundle):
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, created_at=datetime(2026, 11, 22))
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, updated_at=NOW - timedelta(seconds=1))


def test_revision_draft_revision_positive(lock, bundle):
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, draft_revision=0)


def test_published_requirements(lock, bundle):
    make_rev(lock, bundle, published_seq=1)
    for over in (dict(published_seq=None), dict(published_seq=0), dict(bundle=None),
                 dict(job_id=None)):
        with pytest.raises(ValidationError):
            make_rev(lock, bundle, **over)


def test_staged_has_no_run_fields(lock, bundle):
    base = dict(state='staged', job_id=None, brief_id=None, bundle=None, published_seq=None)
    make_rev(lock, bundle, **base)
    for k, v in (('job_id', OTHER), ('brief_id', OTHER), ('bundle', bundle), ('published_seq', 1)):
        with pytest.raises(ValidationError):
            make_rev(lock, bundle, **{**base, k: v})


def test_running_requires_job_and_brief_no_seq_no_bundle(lock, bundle):
    base = dict(state='running', bundle=None, published_seq=None)
    make_rev(lock, bundle, **base)
    for over in (dict(job_id=None), dict(brief_id=None), dict(published_seq=1), dict(bundle=bundle)):
        with pytest.raises(ValidationError):
            make_rev(lock, bundle, **{**base, **over})


@pytest.mark.parametrize('state', ['rejected', 'failed', 'cancelled', 'conflict'])
def test_other_terminal_states_have_no_seq(lock, bundle, state):
    make_rev(lock, bundle, state=state, published_seq=None, bundle=None, job_id=None, brief_id=None)
    make_rev(lock, bundle, state=state, published_seq=None, bundle=None)
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, state=state, published_seq=1, bundle=None)


def test_edit_rules(lock, bundle):
    make_rev(lock, bundle, parent=OTHER)
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, evidence_revision_id=OTHER)
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, derived_from=OTHER)
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, parent=RID)


def test_rebuild_rules(lock, bundle):
    ok = dict(kind='rebuild', derived_from=OTHER, parent=OTHER)
    make_rev(lock, bundle, **ok)
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, **{**ok, 'derived_from': None})
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, **{**ok, 'evidence_revision_id': OTHER})
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, **{**ok, 'derived_from': RID})


def test_restore_rules(lock, bundle):
    ok = dict(kind='restore', derived_from=OTHER, evidence_revision_id='4' * 32, parent='5' * 32)
    make_rev(lock, bundle, **ok)
    for k in ('derived_from', 'evidence_revision_id', 'parent'):
        with pytest.raises(ValidationError):
            make_rev(lock, bundle, **{**ok, k: None})
    # restore is published-only, never staged/running/other terminal
    for state in ('staged', 'running', 'rejected', 'failed', 'cancelled', 'conflict'):
        with pytest.raises(ValidationError):
            make_rev(lock, bundle, **{**ok, 'state': state, 'published_seq': None, 'bundle': None,
                                       'job_id': None, 'brief_id': None})
    with pytest.raises(ValidationError):
        make_rev(lock, bundle, **{**ok, 'derived_from': RID})


# ---- RevisionPayload ----

def test_payload_roundtrip_and_helper(compiled, lock):
    p = rv.build_payload('beam', 'source text', compiled.ir, lock)
    assert isinstance(p, RevisionPayload)
    assert p.source_id == 'beam' and p.source == 'source text'
    assert json.loads(p.ir_json)['schema_version'] == compiled.ir.schema_version
    assert json.loads(p.lock_json)['schema_version'] == lock.schema_version
    assert RevisionPayload.model_validate_json(p.model_dump_json()) == p
    # deterministic
    assert rv.build_payload('beam', 'source text', compiled.ir, lock) == p


def test_payload_source_limit():
    RevisionPayload(source_id='s', source='a' * SRC_CAP, ir_json='{}', lock_json='{}')
    with pytest.raises(ValidationError):
        RevisionPayload(source_id='s', source='a' * (SRC_CAP + 1), ir_json='{}', lock_json='{}')
    e = chr(0xE9)  # 2 bytes in UTF-8
    RevisionPayload(source_id='s', source=e * (SRC_CAP // 2), ir_json='{}', lock_json='{}')
    with pytest.raises(ValidationError):
        RevisionPayload(source_id='s', source=e * (SRC_CAP // 2) + 'a' * 1 + 'b', ir_json='{}',
                        lock_json='{}')


@pytest.mark.parametrize('field', ['ir_json', 'lock_json'])
def test_payload_json_limits(field):
    base = dict(source_id='s', source='x', ir_json='{}', lock_json='{}')
    RevisionPayload(**{**base, field: 'a' * JSON_CAP})
    with pytest.raises(ValidationError):
        RevisionPayload(**{**base, field: 'a' * (JSON_CAP + 1)})
    with pytest.raises(ValidationError):
        RevisionPayload(**{**base, field: ''})


def test_payload_surrogate_and_extra():
    with pytest.raises(ValidationError):
        RevisionPayload(source_id='s', source='a' + chr(0xD800), ir_json='{}', lock_json='{}')
    with pytest.raises(ValidationError):
        RevisionPayload(source_id='s', source='x', ir_json='{}', lock_json='{}', extra=1)
    with pytest.raises(ValidationError):
        RevisionPayload(source_id='', source='x', ir_json='{}', lock_json='{}')


# ---- JournalEntry ----

def entry(**over):
    d = dict(seq=1, program_id=PID, revision_id=RID, event='staged', detail={}, created_at=NOW)
    d.update(over)
    return JournalEntry(**d)


def test_journal_roundtrip():
    e = entry(detail={'a_b': 'x', 'n': 3, 'ok': True, 'none': None})
    again = JournalEntry.model_validate_json(e.model_dump_json())
    assert again == e and again.schema_version == PROGRAM_JOURNAL_VERSION
    assert entry(revision_id=None).revision_id is None


def test_journal_closed_event_and_ids():
    for bad in (dict(event='nope'), dict(event=''), dict(seq=0), dict(program_id='x'),
                dict(revision_id='x'), dict(created_at=datetime(2026, 1, 1)),
                dict(schema_version='other')):
        with pytest.raises(ValidationError):
            entry(**bad)
    with pytest.raises(ValidationError):
        JournalEntry(seq=1, program_id=PID, revision_id=None, event='staged', detail={},
                     created_at=NOW, extra=1)
    for ev in JOURNAL_EVENTS:
        entry(event=ev)


def test_journal_detail_rules():
    with pytest.raises(ValidationError):
        entry(detail={'a': {'b': 1}})
    with pytest.raises(ValidationError):
        entry(detail={'a': [1]})
    with pytest.raises(ValidationError):
        entry(detail={'a': 1.5})
    with pytest.raises(ValidationError):
        entry(detail={'a': 'caf' + chr(0xE9)})
    # 16 keys ok, 17 not
    entry(detail={f'k{chr(97 + i)}': i for i in range(16)})
    with pytest.raises(ValidationError):
        entry(detail={f'k{chr(97 + i)}': i for i in range(17)})
    # value length
    entry(detail={'a': 'x' * 240})
    with pytest.raises(ValidationError):
        entry(detail={'a': 'x' * 241})
    # key rules
    entry(detail={'a' * 32: 1})
    for bad in ('a' * 33, '', 'A', 'a-b', 'a1', 'a b', 'caf' + chr(0xE9)):
        with pytest.raises(ValidationError):
            entry(detail={bad: 1})


def test_journal_detail_int_bounds():
    entry(detail={'n': 2 ** 53})
    entry(detail={'n': -(2 ** 53)})
    with pytest.raises(ValidationError):
        entry(detail={'n': 2 ** 53 + 1})


# ---- RevisionView ----

def test_view(lock, bundle):
    r = make_rev(lock, bundle)
    for a in ('available', 'expired', 'missing', 'none'):
        v = RevisionView(revision=r, artifacts=a, is_current=True)
        assert RevisionView.model_validate_json(v.model_dump_json()) == v
    with pytest.raises(ValidationError):
        RevisionView(revision=r, artifacts='gone', is_current=True)
    with pytest.raises(ValidationError):
        RevisionView(revision=r, artifacts='none', is_current=True, extra=1)


# ---- ASCII + import pin ----

def test_source_files_ascii_and_lf():
    for p in (ROOT / 'backend/engineering/program/revisions.py', Path(__file__)):
        data = p.read_bytes()
        data.decode('ascii')
        assert b'\r' not in data


def test_import_loads_no_native_provider_or_web_module():
    code = (
        'import sys, json\n'
        'import engineering.program.revisions\n'
        'print(json.dumps(sorted(m for m in ("cadquery", "OCP", "google.genai", "fastapi", "httpx")'
        ' if m in sys.modules)))\n')
    env = dict(os.environ, PYTHONPATH=f'{ROOT / "backend"}{os.pathsep}{ROOT}')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env,
                         timeout=120, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []

"""F06 task 2: pure revision diff (D8), evidence summaries, caps and schema."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from engineering.contracts import (
    ArtifactIdentity, BundleIdentity, EngineeringRun, Lifecycle, Outcome,
)
from engineering.language.semantic import compile_source
from engineering.program import revisions as rv
from engineering.program.hashing import (
    canonical_json, execution_lock_v0_2_sha256, semantic_sha256, source_sha256,
)
from engineering.program.ir import execution_lock_v0_2
from engineering.program.kernel import NativeVersion, ProbeResult, backend_lock, make_description
from engineering.program.revisions import (
    MAX_DIFF_CHANGES, MAX_DIFF_VALUE_CHARS, DiffEntry, EvidenceSummary, ProgramRevision,
    RevisionDiff, diff, evidence_from_run,
)
from engineering.program.schema_export import SCHEMA_DIR, SCHEMA_MODELS, check

TESTS = Path(__file__).resolve().parent
P03 = TESTS / 'fixtures' / 'program' / 'p03'
F06 = TESTS / 'fixtures' / 'program' / 'f06'
BACKEND = TESTS.parent
NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
H = 'a' * 64
RA, RB, PID, OTHER = '1' * 32, '4' * 32, '2' * 32, '3' * 32


def _compile(path: Path):
    return compile_source(path.read_bytes())


def _lock(ir, version='2.6.1'):
    desc = make_description('occt', mode='native', adapter_version='adapter_1', tolerances=())
    probe = ProbeResult(backend='occt', mode='native', available=True,
                        native_versions=(NativeVersion(name='cadquery', version=version),
                                         NativeVersion(name='OCP', version='7.8.1')))
    return execution_lock_v0_2(ir, backend_lock(desc, probe))


def _bundle(manifest='c' * 64):
    names = ('brief.json', 'selected_spec.json', 'model.py', 'model.step', 'preview.glb',
             'attempts.json', 'report.json', 'report.md')
    return BundleIdentity(payloads=tuple(ArtifactIdentity(path=n, sha256=H, bytes=10) for n in names),
                          manifest_sha256=manifest, manifest_bytes=10, zip_sha256=H, zip_bytes=10,
                          stored_bytes=200)


def _rev(rid, compiled, lock, *, source_bytes=None, fixed='f' * 64, brief=H):
    ir = compiled.ir
    return ProgramRevision(
        revision_id=rid, program_id=PID, parent=None, kind='edit', derived_from=None,
        evidence_revision_id=rid, state='published', reason=None,
        source_sha256=source_sha256(source_bytes if source_bytes is not None else rid),
        semantic_sha256=semantic_sha256(ir), brief_sha256=brief, fixed_requirements_sha256=fixed,
        lock_sha256=execution_lock_v0_2_sha256(lock), lock=lock, draft_id=OTHER, draft_revision=1,
        brief_id=OTHER, job_id=OTHER, bundle=_bundle(), published_seq=1, created_at=NOW,
        updated_at=NOW)


@pytest.fixture(scope='module')
def base():
    c = _compile(P03 / 'beam.mir')
    return c, _lock(c.ir)


@pytest.fixture(scope='module')
def rev_base(base):
    c, lock = base
    return _rev(RA, c, lock, source_bytes=(P03 / 'beam.mir').read_bytes())


def _entries(d, section):
    return [e for e in d.changes if e.section == section]


# ---- hashes / identical / whitespace ----

def test_identical_revisions_have_no_changes(base, rev_base):
    c, lock = base
    other = rev_base.model_copy(update={'revision_id': RB, 'evidence_revision_id': RB})
    d = diff(rev_base, c.ir, other, c.ir)
    assert d.changes == () and d.change_count == 0 and d.changes_truncated is False
    assert d.hashes.model_dump() == dict(source=True, semantic=True, brief=True, fixed_requirements=True,
                                         lock=True)
    assert d.requirements_changed is False and d.detail_unavailable is None
    assert (d.a, d.b) == (RA, RB) and d.schema_version == 'mirum_revision_diff_v0_1'


def test_whitespace_and_comment_change_is_source_only(base, rev_base):
    c, lock = base
    ws = _compile(F06 / 'beam_whitespace.mir')
    rev_ws = _rev(RB, ws, _lock(ws.ir), source_bytes=(F06 / 'beam_whitespace.mir').read_bytes())
    d = diff(rev_base, c.ir, rev_ws, ws.ir)
    assert d.hashes.source is False
    assert d.hashes.semantic and d.hashes.lock
    assert d.changes == () and d.change_count == 0


# ---- content changes ----

def test_tip_force_change_is_a_requirements_entry(base, rev_base):
    c, lock = base
    t = _compile(F06 / 'beam_tip60.mir')
    rev_t = _rev(RB, t, _lock(t.ir), fixed='e' * 64)
    d = diff(rev_base, c.ir, rev_t, t.ir)
    assert d.requirements_changed is True
    assert d.hashes.fixed_requirements is False and d.hashes.semantic is False
    reqs = _entries(d, 'requirements')
    assert len(reqs) == 1
    e = reqs[0]
    assert e.field == 'value' and 'tip_force' in e.key
    assert e.before != e.after and e.before is not None and e.after is not None
    assert '50.0' in e.before and '60.0' in e.after
    assert {x.section for x in d.changes} == {'requirements'}


def test_candidate_heights_change_is_a_candidates_entry(base, rev_base):
    c, lock = base
    h = _compile(F06 / 'beam_heights.mir')
    rev_h = _rev(RB, h, _lock(h.ir))
    d = diff(rev_base, c.ir, rev_h, h.ir)
    cand = _entries(d, 'candidates')
    assert len(cand) == 1 and cand[0].field == 'values'
    assert cand[0].before != cand[0].after
    assert d.requirements_changed is False
    assert {x.section for x in d.changes} == {'candidates'}


def test_backend_lock_version_change_is_a_backend_lock_entry(base, rev_base):
    c, lock = base
    lock2 = _lock(c.ir, version='2.7.0')
    rev2 = _rev(RB, c, lock2)
    d = diff(rev_base, c.ir, rev2, c.ir)
    assert d.hashes.lock is False and d.hashes.semantic is True
    assert [(e.section, e.key) for e in d.changes] == [('backend_lock', 'native_versions')]
    assert '2.6.1' in d.changes[0].before and '2.7.0' in d.changes[0].after


def test_lock_top_level_change_is_a_lock_entry(base, rev_base):
    c, lock = base
    lock2 = lock.model_copy(update={'compiler_version': 'other_compiler_9'})
    rev2 = _rev(RB, c, lock2)
    d = diff(rev_base, c.ir, rev2, c.ir)
    assert [(e.section, e.key, e.field) for e in d.changes] == [('lock', 'compiler_version', 'value')]
    assert d.changes[0].after == canonical_json('other_compiler_9')


def test_execution_change_is_an_execution_entry(base, rev_base):
    c, lock = base
    ir2 = c.ir.model_copy(update={'execution': c.ir.execution.model_copy(update={'exports': ('step',)})})
    rev2 = _rev(RB, c, lock)
    d = diff(rev_base, c.ir, rev2, ir2)
    assert [(e.section, e.key) for e in d.changes] == [('execution', 'exports')]


def test_added_and_removed_items_use_whole_field_marker(base, rev_base):
    c, lock = base
    req = c.ir.requirements.requirements
    ir2 = c.ir.model_copy(update={'requirements': c.ir.requirements.model_copy(
        update={'requirements': req[:-1]})})
    rev2 = _rev(RB, c, lock)
    d = diff(rev_base, c.ir, rev2, ir2)
    assert len(d.changes) == 1
    e = d.changes[0]
    assert (e.section, e.field) == ('requirements', '*') and e.before is not None and e.after is None
    back = diff(rev2, ir2, rev_base, c.ir).changes[0]
    assert back.before is None and back.after == e.before


# ---- compacted ----

@pytest.mark.parametrize('a_none,b_none', [(True, False), (False, True), (True, True)])
def test_compacted_side_is_hash_only(base, rev_base, a_none, b_none):
    c, lock = base
    t = _compile(F06 / 'beam_tip60.mir')
    rev_t = _rev(RB, t, _lock(t.ir), fixed='e' * 64)
    d = diff(rev_base, None if a_none else c.ir, rev_t, None if b_none else t.ir)
    assert d.detail_unavailable == 'history_expired'
    assert d.changes == () and d.change_count == 0
    assert d.hashes.semantic is False and d.requirements_changed is True


def test_compacted_diff_still_carries_evidence(base, rev_base):
    c, lock = base
    other = rev_base.model_copy(update={'revision_id': RB, 'evidence_revision_id': RB})
    ev = EvidenceSummary(lifecycle='completed', outcome='accepted', attempts=())
    d = diff(rev_base, None, other, None, evidence_a=ev, evidence_b=None)
    assert d.evidence_a == ev and d.evidence_b is None


# ---- evidence ----



def _run():
    from tests import test_engineering_beam_golden as g
    return g._run()


def test_evidence_from_engineering_run():
    run = _run()
    ev = evidence_from_run(run)
    att = run.attempts[0]
    assert ev.lifecycle == 'completed' and ev.outcome == 'accepted' and ev.expired is False
    assert ev.selected_candidate_sha256 == run.selected_candidate_sha256
    assert len(ev.attempts) == 1
    assert ev.attempts[0].candidate_sha256 == att.candidate.candidate_sha256
    assert ev.attempts[0].outcome == 'accepted' and ev.attempts[0].failure_count == 0
    assert {c.check_id for c in ev.attempts[0].checks} == {c.check_id for c in att.checks}
    assert all(c.state == 'passed' for c in ev.attempts[0].checks)
    assert ev.bundle.manifest_sha256 == run.bundle.manifest_sha256
    assert ev.bundle.zip_sha256 == run.bundle.zip_sha256
    assert [(p.path, p.sha256) for p in ev.bundle.payloads] == [
        (p.path, p.sha256) for p in run.bundle.payloads]


def test_evidence_from_expired_run():
    from engineering.jobs import ExpiredRun, parse_run
    run = _run()
    expired = ExpiredRun(
        schema_version='engineering_expired_run_v1', job_id=run.job_id, capability=run.capability,
        brief_sha256=run.brief_sha256, fixed_requirements_sha256=run.fixed_requirements_sha256,
        lifecycle=run.lifecycle, outcome=run.outcome, created_at=run.created_at,
        updated_at=run.updated_at, expired=True)
    ev = evidence_from_run(parse_run(expired.model_dump_json()))
    assert ev.expired is True and ev.attempts == () and ev.bundle is None
    assert ev.selected_candidate_sha256 is None
    assert (ev.lifecycle, ev.outcome) == ('completed', 'accepted')


def test_each_side_uses_its_own_evidence(base, rev_base):
    c, lock = base
    run_a = _run()
    bundle_b = _bundle(manifest='9' * 64)
    run_b = run_a.model_copy(update={'job_id': 'beam-job-2', 'bundle': bundle_b})
    other = rev_base.model_copy(update={'revision_id': RB, 'evidence_revision_id': RB})
    ea, eb = evidence_from_run(run_a), evidence_from_run(run_b)
    d = diff(rev_base, c.ir, other, c.ir, evidence_a=ea, evidence_b=eb)
    assert d.evidence_a == ea and d.evidence_b == eb and ea != eb
    assert d.evidence_a.bundle.manifest_sha256 == run_a.bundle.manifest_sha256
    assert d.evidence_b.bundle.manifest_sha256 == '9' * 64
    assert d.changes == ()


def test_evidence_summary_is_closed_and_frozen():
    ev = EvidenceSummary(lifecycle='completed', outcome='accepted', attempts=())
    with pytest.raises(ValidationError):
        EvidenceSummary(lifecycle='completed', outcome='accepted', attempts=(), extra=1)
    with pytest.raises(ValidationError):
        ev.outcome = 'rejected'  # type: ignore[misc]


# ---- caps ----

def test_over_cap_value_becomes_marker_with_sha(base, rev_base):
    c, lock = base
    big = 'x' * (MAX_DIFF_VALUE_CHARS + 50)
    lock2 = lock.model_copy(update={'compiler_version': big})
    d = diff(rev_base, c.ir, _rev(RB, c, lock2), c.ir)
    (e,) = d.changes
    text = canonical_json(big)
    import hashlib
    assert e.after == f'<over_limit sha256={hashlib.sha256(text.encode()).hexdigest()}>'
    assert len(e.after) <= MAX_DIFF_VALUE_CHARS
    assert e.before == canonical_json(lock.compiler_version)


def test_exactly_at_cap_is_kept_verbatim(base, rev_base):
    c, lock = base
    at_cap = 'y' * (MAX_DIFF_VALUE_CHARS - 2)  # plus two quotes == cap
    lock2 = lock.model_copy(update={'compiler_version': at_cap})
    (e,) = diff(rev_base, c.ir, _rev(RB, c, lock2), c.ir).changes
    assert e.after == canonical_json(at_cap) and len(e.after) == MAX_DIFF_VALUE_CHARS


def test_entry_cap_truncates_and_reports_total(base, rev_base, monkeypatch):
    c, lock = base
    many = [DiffEntry(section='lock', key=f'k{i:04d}', field='value', before='1', after='2')
            for i in range(MAX_DIFF_CHANGES + 10)]
    monkeypatch.setattr(rv, '_flat_entries', lambda section, a, b: iter(many) if section == 'lock' else iter(()))
    other = rev_base.model_copy(update={'revision_id': RB, 'evidence_revision_id': RB})
    d = diff(rev_base, c.ir, other, c.ir)
    assert len(d.changes) == MAX_DIFF_CHANGES == 512
    assert d.change_count == MAX_DIFF_CHANGES + 10 and d.changes_truncated is True
    assert d.changes == tuple(many[:MAX_DIFF_CHANGES])


def test_model_rejects_more_than_cap():
    e = DiffEntry(section='lock', key='k', field='value', before='1', after='2')
    h = rv.HashEquality(source=True, semantic=True, brief=True, fixed_requirements=True, lock=True)
    with pytest.raises(ValidationError):
        RevisionDiff(a=RA, b=RB, hashes=h, requirements_changed=False,
                     changes=(e,) * (MAX_DIFF_CHANGES + 1))
    with pytest.raises(ValidationError):
        DiffEntry(section='bogus', key='k', field='value')
    with pytest.raises(ValidationError):
        DiffEntry(section='lock', key='k', field='value', before='z' * (MAX_DIFF_VALUE_CHARS + 1))


def test_diff_is_deterministic_and_ascii(base, rev_base):
    c, lock = base
    t = _compile(F06 / 'beam_tip60.mir')
    rev_t = _rev(RB, t, _lock(t.ir), fixed='e' * 64)
    one = diff(rev_base, c.ir, rev_t, t.ir).model_dump_json()
    two = diff(rev_base, c.ir, rev_t, t.ir).model_dump_json()
    assert one == two and one.isascii()


# ---- schema / import pin ----

def test_schema_registered_and_in_sync():
    assert SCHEMA_MODELS['mirum_revision_diff_v0_1.schema.json'] is RevisionDiff
    assert (SCHEMA_DIR / 'mirum_revision_diff_v0_1.schema.json').is_file()
    assert check() == []


def test_import_pin_still_pure():
    code = ('import json,sys; import engineering.program.revisions; '
            'print(json.dumps(sorted(m for m in ("cadquery","OCP","google.genai","fastapi","httpx",'
            '"engineering.jobs") if m in sys.modules)))')
    out = subprocess.run([sys.executable, '-c', code], cwd=BACKEND, capture_output=True, text=True,
                         check=True).stdout
    assert json.loads(out) == []

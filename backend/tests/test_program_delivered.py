"""F07 task 3: DeliveredVerifier, EvidenceStore and method_version.

Host tests (Windows OK) drive the verifier with a synthetic published job: a fake publisher serving
in-memory payloads, a HostStore jobs row and a measurement-replay adapter whose FaceTable comes from the
f07 corpus. The posix tests (CI native shards) run the same verifier over a real BundlePublisher,
JobController and SQLiteStore; the adapter is a measurement replay there too, the native tests of
another task use the real OCCT measure.

resign_bundle (real publisher) and HostWorld.resign (fake publisher) rewrite a published file and keep
the manifest, archive, publication row and jobs row consistent, so publisher.validate still passes and
only the verifier's cross-checks can catch the tampering. Task 5 reuses resign_bundle.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import io
import json
import os
import sqlite3
import subprocess
import sys
import time
import zipfile
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from engineering.bundles import FORMATS, MIME, METADATA_RESERVE, BundleManifest, digest, encode
from engineering.contracts import (
    ArtifactIdentity, AttemptRecord, BundleIdentity, EngineeringRun,
)
from engineering.jobs import changed
from engineering.program import delivered as dv
from engineering.program.adapters import adapter_for
from engineering.program.adapters.occt import MeasureOutcome
from engineering.program.delivered import (
    DELIVERED_BINDING_AMBIGUOUS, DELIVERED_CHECK_FAILED, DELIVERED_EVIDENCE_STALE,
    DELIVERED_EVIDENCE_UNAVAILABLE, DELIVERED_REASONS, DELIVERED_UNVERIFIED, DeliveredVerifier,
    EvidenceRowInvalid, EvidenceStore, compute_method_version, method_version, parse_json_bounded,
    read_capped,
)
from engineering.program.evidence import (
    UNKNOWN_SHA256, DeliveredEvidence, FaceTable, MeshSummary, build_check_plan, check_plan_sha256,
    evidence_sha256,
)
from engineering.program.planning import _candidates_from_content
from engineering.program.revisions import ProgramRevision, RevisionRefusal, build_payload
from engineering.program.transactions import RevisionService
from tests.test_program_revision_records import compiled, lock, make_rev  # noqa: F401
from tests.test_program_revisions import HostStore, clock  # noqa: F401  (clock: fixture)

ROOT = Path(__file__).resolve().parents[2]
PROGRAM = ROOT / 'backend' / 'engineering' / 'program'
TABLES = Path(__file__).resolve().parent / 'fixtures' / 'program' / 'f07' / 'face_tables'
NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
JOB = '4' * 32
JOB2 = '5' * 32
BRIEF = '6' * 32
REV1 = '7' * 32
REV2 = '8' * 32
STEP = b'ISO-10303-21; synthetic step bytes'
GLB = b'glTF synthetic glb bytes'
posix_only = pytest.mark.skipif(os.name != 'posix', reason='native service tests need posix SQLiteStore')


# --------------------------------------------------------------------------- shared helpers

def fixture_case(name):
    return json.loads((TABLES / f'{name}.json').read_text(encoding='utf-8'))


def face_table(name='ideal_box_h15', versions=None):
    raw = fixture_case(name)['table']
    if versions is not None:
        raw = dict(raw, native_versions=[dict(name=n, version=v) for n, v in versions])
    return FaceTable.model_validate_json(json.dumps(raw))


def fixture_mesh(name='ideal_box_h15'):
    return MeshSummary.model_validate_json(json.dumps(fixture_case(name)['mesh']))


def lock_versions(rev):
    return tuple((v.name, v.version) for v in rev.lock.backend_lock.native_versions)


class MeasureAdapter:
    """Measurement replay: returns a canned MeasureOutcome and records every call."""
    mode = 'native'

    def __init__(self, table=None, mesh=None, *, delay=0.0, boom=None, reason=None, hold=None):
        self.table, self.mesh, self.delay, self.boom, self.reason = table, mesh, delay, boom, reason
        self.calls = []
        self.active = 0
        self.peak = 0

    async def measure(self, step, glb, plan):
        self.calls.append((step, glb, plan))
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.boom is not None:
                raise self.boom
            return MeasureOutcome(self.table, self.mesh, self.reason)
        finally:
            self.active -= 1


def factory(adapter):
    return lambda backend, mode='native': adapter


def with_bundle(rev, bundle):
    return ProgramRevision.model_validate(dict(rev.model_dump(), bundle=bundle))


def unbundled(rev):
    """The revision as the settle path sees it: running, job linked, bundle not yet recorded."""
    return evolve(rev, state='running', bundle=None, published_seq=None)


def evolve(rev, **changes):
    return ProgramRevision.model_validate(dict(rev.model_dump(), **changes))


# --------------------------------------------------------------------------- host world

def _check(cand, check_id, state, step_sha):
    return dict(check_id=check_id, state=state, method='m', method_version='1',
                brief_sha256=cand.brief_sha256, candidate_sha256=cand.candidate_sha256,
                measurements=[dict(name='x', value=1.0, unit='mm')],
                limits=[dict(name='x', value=2.0, unit='mm', comparison='le')],
                artifacts=[dict(path='model.step', sha256=step_sha, bytes=len(STEP))],
                reason=None if state == 'passed' else 'Stress check failed')


def _attempt(index, cand, outcome, step_sha):
    from engineering.capabilities import CAPABILITY
    checks = [_check(cand, cid, 'passed', step_sha) for cid in CAPABILITY.required_checks]
    failures = []
    if outcome == 'rejected':
        checks[0] = _check(cand, checks[0]['check_id'], 'failed', step_sha)
        failures = ['Stress check failed']
    return AttemptRecord.model_validate_json(json.dumps(dict(
        ordinal=index, candidate=cand.model_dump(mode='json'), outcome=outcome, checks=checks,
        failures=failures)))


def _identities(files):
    return tuple(ArtifactIdentity(path=name, bytes=len(data), sha256=digest(data))
                 for name, data in sorted(files.items()))


def _bundle(files):
    payloads = _identities(files)
    manifest = encode([p.model_dump() for p in payloads])  # derived from the files: it moves on re-sign
    archive = b'PK-synthetic-archive' + manifest
    stored = sum(len(v) for v in files.values()) + len(manifest) + len(archive) + METADATA_RESERVE
    return BundleIdentity(payloads=payloads, manifest_sha256=digest(manifest), manifest_bytes=len(manifest),
                          zip_sha256=digest(archive), zip_bytes=len(archive), stored_bytes=stored)


class FakeDownload:
    def __init__(self, data, owner):
        self.file, self.size, self.sha256, self.owner, self.closed = io.BytesIO(data), len(data), '', owner, False

    def close(self):
        if not self.closed:
            self.closed = True
            self.owner.closed += 1


class FakePublisher:
    """Serves in-memory payloads. validate() and open_lease() check the envelope like the real one."""

    def __init__(self, files, bundle):
        self.files, self.bundle = dict(files), bundle
        self.validated = self.opened = self.closed = 0
        self.fail_validate = False

    def validate(self, job_id, envelope=None):
        self.validated += 1
        if self.fail_validate or envelope != self.bundle:
            raise ValueError('Package fingerprint changed')

    def open_lease(self, job_id, fmt, *, envelope=None):
        if envelope != self.bundle:
            raise ValueError('Publication envelope changed')
        self.opened += 1
        return FakeDownload(self.files[FORMATS[fmt][0]], self)


class HostWorld:
    """A synthetic accepted beam job (5 and 10 mm rejected, 15 mm accepted) behind a FakePublisher."""

    def __init__(self, compiled, lock, *, job=JOB, revision_id=REV1):
        self.compiled, self.lock, self.job, self.store = compiled, lock, job, HostStore()
        self.ir = compiled.ir
        content = compiled.brief_content
        self.content = content
        self.candidates = {c.specification.height_mm: c for c in _candidates_from_content(content)}
        assert sorted(self.candidates) == [5.0, 10.0, 15.0]
        self.selected = self.candidates[15.0]
        step_sha = digest(STEP)
        self.attempts = [_attempt(0, self.candidates[5.0], 'rejected', step_sha),
                         _attempt(1, self.candidates[10.0], 'rejected', step_sha),
                         _attempt(2, self.selected, 'accepted', step_sha)]
        spec = self.selected.specification
        self.report = dict(
            schema_version='engineering_report_v1', job_id=job, brief_sha256=content.brief_sha256,
            fixed_requirements_sha256=content.fixed_requirements_sha256, outcome='accepted',
            evaluations=[dict(candidate_sha256=c.candidate_sha256, report_sha256='a' * 64,
                              native_report=dict(physics=dict(state='passed'), measurements=dict(
                                  length_mm=spec.length_mm, width_mm=spec.width_mm,
                                  height_mm=c.specification.height_mm,
                                  section_iy_mm4=c.specification.width_mm * c.specification.height_mm ** 3 / 12.0)))
                         for c in self.candidates.values()])
        files = {'brief.json': b'{}\n', 'model.py': b'x = 1\n', 'report.md': b'# report\n',
                 'model.step': STEP, 'preview.glb': GLB,
                 'selected_spec.json': encode(spec.model_dump(mode='json')),
                 'attempts.json': encode([a.model_dump(mode='json') for a in self.attempts]),
                 'report.json': encode(self.report)}
        self.pub = FakePublisher(files, _bundle(files))
        with self.store.transaction() as db:
            db.execute('INSERT INTO briefs VALUES (?,?)', (BRIEF, '{}'))
        self.revision = make_rev(
            lock, self.pub.bundle, revision_id=revision_id, evidence_revision_id=revision_id, job_id=job,
            brief_sha256=content.brief_sha256, fixed_requirements_sha256=content.fixed_requirements_sha256,
            semantic_sha256=compiled.semantic_sha256)
        self.write_run(self.accepted_run())

    def accepted_run(self, **over):
        data = dict(
            schema_version='engineering_run_v1', job_id=self.job,
            capability=self.selected.content.capability.model_dump(mode='json'),
            brief_sha256=self.content.brief_sha256, fixed_requirements_sha256=self.content.fixed_requirements_sha256,
            lifecycle='completed', outcome='accepted', attempts=[a.model_dump(mode='json') for a in self.attempts],
            created_at=NOW.isoformat(), updated_at=NOW.isoformat(),
            selected_candidate_sha256=self.selected.candidate_sha256,
            bundle=self.pub.bundle.model_dump(mode='json'))
        data.update(over)
        return EngineeringRun.model_validate_json(json.dumps(data))

    def rejected_run(self):
        attempts = [_attempt(i, self.candidates[h], 'rejected', digest(STEP))
                    for i, h in enumerate((5.0, 10.0, 15.0))]
        return self.accepted_run(outcome='rejected', attempts=[a.model_dump(mode='json') for a in attempts],
                                 selected_candidate_sha256=None, bundle=None)

    def write_run(self, run, job=None):
        with self.store.transaction() as db:
            db.execute('DELETE FROM jobs WHERE id=?', (job or self.job,))
            db.execute('INSERT INTO jobs(id, brief, record) VALUES (?,?,?)',
                       (job or self.job, BRIEF, run.model_dump_json()))

    def resign(self, **replacements):
        """Rewrite published files and keep the bundle, the jobs row and the revision consistent."""
        self.pub.files.update({FORMATS[k][0] if k in FORMATS else k: v for k, v in replacements.items()})
        self.pub.bundle = _bundle(self.pub.files)
        run = self.accepted_run()
        self.write_run(run)
        self.revision = with_bundle(self.revision, self.pub.bundle)

    def verify(self, adapter, *, revision=None, timeout_s=5.0, evidence=None):
        verifier = DeliveredVerifier(self.store, self.pub, factory(adapter), evidence_store=evidence,
                                     timeout_s=timeout_s)
        self.verifier = verifier
        return asyncio.run(verifier.verify(revision or self.revision, self.ir))

    def good(self, **kw):
        return MeasureAdapter(face_table(versions=lock_versions(self.revision)), fixture_mesh(), **kw)


@pytest.fixture
def hw(compiled, lock):
    return HostWorld(compiled, lock)


def reasons_of(record):
    return tuple(record.reasons)


# --------------------------------------------------------------------------- method version, constants

def test_method_version_covers_all_delivered_verifier_sources():
    assert [p.name for p in dv.METHOD_SOURCES] == [
        'evidence.py', 'delivered.py', '_occt_measure.py', 'occt.py', '_editable_plate_worker.py',
        '_thermal_worker.py', 'editable_plate_adapter.py', 'thermal_bar.py', 'thermal_bar_adapter.py',
        'mirum_editable_plate.py', 'structural.py', 'thermal.py', 'beam_fea.py',
    ]
    assert all(p.is_file() for p in dv.METHOD_SOURCES)
    assert dv.METHOD_SOURCES[0].parent == PROGRAM and dv.METHOD_SOURCES[2].parent == PROGRAM / 'adapters'
    assert dv.METHOD_SOURCES[3].parent == PROGRAM / 'adapters'
    value = method_version()
    assert value == compute_method_version(dv.METHOD_SOURCES) == method_version()
    assert len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def test_method_version_changes_when_one_source_changes_and_is_name_sorted(tmp_path):
    paths = []
    for name in ('evidence.py', 'delivered.py', '_occt_measure.py'):
        path = tmp_path / name
        path.write_bytes(name.encode() + b' v1')
        paths.append(path)
    base = compute_method_version(paths)
    assert compute_method_version(reversed(paths)) == base
    for path in paths:
        original = path.read_bytes()
        path.write_bytes(original + b' changed')
        assert compute_method_version(paths) != base
        path.write_bytes(original)
        assert compute_method_version(paths) == base


def test_reason_constants_are_the_d7_vocabulary():
    assert DELIVERED_REASONS == (
        'delivered_unverified', 'delivered_evidence_stale', 'delivered_check_failed',
        'delivered_binding_ambiguous', 'delivered_evidence_unavailable')
    assert (DELIVERED_UNVERIFIED, DELIVERED_EVIDENCE_STALE, DELIVERED_CHECK_FAILED,
            DELIVERED_BINDING_AMBIGUOUS, DELIVERED_EVIDENCE_UNAVAILABLE) == DELIVERED_REASONS


def test_import_loads_no_native_provider_or_web_module():
    code = (
        'import sys, json\n'
        'import engineering.program.evidence, engineering.program.delivered\n'
        'print(json.dumps(sorted(m for m in ("cadquery", "OCP", "trimesh", "google.genai", "genai", '
        '"fastapi", "httpx") if m in sys.modules)))\n')
    env = dict(os.environ, PYTHONPATH=f'{ROOT / "backend"}{os.pathsep}{ROOT}')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env,
                         timeout=120, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


def test_delivered_module_is_ascii_lf():
    text = (PROGRAM / 'delivered.py').read_bytes()
    assert text.isascii() and b'\r' not in text


# --------------------------------------------------------------------------- read budgets

def test_read_capped_allows_exactly_the_cap_and_refuses_one_more():
    assert read_capped(io.BytesIO(b'x' * 10), 10) == b'x' * 10
    assert read_capped(io.BytesIO(b''), 10) == b''
    with pytest.raises(dv._Problem) as info:
        read_capped(io.BytesIO(b'x' * 11), 10)
    assert (info.value.kind, info.value.reason) == ('unavailable', 'measurement_unavailable')


def test_budget_constants_match_the_spec():
    assert dv.MAX_BINARY_BYTES == 32 * 1024 ** 2 and dv.MAX_JSON_BYTES == 1024 ** 2
    assert dv.MEASURE_TIMEOUT_S == 60.0
    caps = {name: cap for _, name, cap in dv._PAYLOADS}
    assert caps == {'model.step': dv.MAX_BINARY_BYTES, 'preview.glb': dv.MAX_BINARY_BYTES,
                    'report.json': dv.MAX_JSON_BYTES, 'selected_spec.json': dv.MAX_JSON_BYTES,
                    'attempts.json': dv.MAX_JSON_BYTES}


def test_parse_json_bounded_budgets():
    assert parse_json_bounded(b'{"a": [1, 2.5]}') == {'a': [1, 2.5]}
    for bad in (b'NaN', b'[Infinity]', b'{"a": -Infinity}', b'{', bytes([0xff]), b''):
        with pytest.raises(dv._Problem):
            parse_json_bounded(bad)
    with pytest.raises(dv._Problem):
        parse_json_bounded(b'[' + b'0' * 20 + b']', cap=10)
    ok = b'[' * dv.MAX_JSON_DEPTH + b']' * dv.MAX_JSON_DEPTH
    assert parse_json_bounded(ok) is not None
    deep = b'[' * (dv.MAX_JSON_DEPTH + 1) + b']' * (dv.MAX_JSON_DEPTH + 1)
    with pytest.raises(dv._Problem):
        parse_json_bounded(deep)
    with pytest.raises(dv._Problem):  # far past the interpreter recursion limit
        parse_json_bounded(b'[' * 100000 + b']' * 100000, cap=10 ** 6)


# --------------------------------------------------------------------------- verified record and store

def test_verify_stores_a_verified_record_bound_to_the_exact_bytes(hw):
    adapter = hw.good()
    record = hw.verify(adapter)
    assert record.verdict == 'verified' and record.reasons == ()
    assert record.revision_id == REV1 and record.job_id == JOB
    assert record.candidate_sha256 == hw.selected.candidate_sha256
    assert record.manifest_sha256 == hw.pub.bundle.manifest_sha256
    assert record.step_sha256 == digest(STEP) and record.glb_sha256 == digest(GLB)
    assert record.method_version == method_version()
    plan = build_check_plan(hw.ir, hw.revision.lock, tuple(hw.candidates.values()), mesh_convention_pinned=True)
    assert record.check_plan_sha256 == check_plan_sha256(plan)
    assert record.native_versions == hw.revision.lock.backend_lock.native_versions
    (step, glb, build_plan), = adapter.calls
    assert step.data == STEP and glb.data == GLB and step.ref.format == 'step' and glb.ref.format == 'glb'
    assert build_plan.candidate == hw.selected
    # five leases opened and every one closed
    assert hw.pub.opened == 5 and hw.pub.closed == 5 and hw.pub.validated == 1
    stored = hw.verifier.evidence.get(None, REV1)
    assert stored == record
    with hw.store.transaction() as db:
        assert hw.verifier.evidence.match_problem(db, REV1, job_id=JOB, bundle=hw.pub.bundle) is None


def test_evidence_store_creates_its_table_without_touching_user_version():
    store = HostStore()
    before = store.db.execute('PRAGMA user_version').fetchone()[0]
    EvidenceStore(store)
    EvidenceStore(store)  # idempotent
    after = store.db.execute('PRAGMA user_version').fetchone()[0]
    assert before == after
    info = store.db.execute('PRAGMA table_info(program_evidence)').fetchall()
    assert [r['name'] for r in info] == ['revision_id', 'job_id', 'manifest_sha256', 'step_sha256',
                                         'check_plan_sha256', 'method_version', 'verdict', 'record', 'created']
    assert [r['name'] for r in info if r['pk']] == ['revision_id']
    assert [r['notnull'] for r in info] == [0, 1, 1, 0, 1, 1, 1, 1, 1]
    assert store.db.execute('PRAGMA foreign_key_list(program_evidence)').fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute("INSERT INTO program_evidence VALUES ('r','j','m',NULL,'c','v','accepted','{}',1.0)")


def test_put_never_overwrites_a_verified_row_but_replaces_the_rest(compiled, lock):
    bad = HostWorld(compiled, lock)
    bad.pub.fail_validate = True
    store = bad.store
    evidence = EvidenceStore(store)
    unavailable = bad.verify(bad.good(), evidence=evidence)
    assert unavailable.verdict == 'unavailable'
    assert evidence.get(None, REV1) == unavailable
    # a later verified record for the same revision replaces the unavailable one
    bad.pub.fail_validate = False
    verified = bad.verify(bad.good(), evidence=evidence)
    assert verified.verdict == 'verified' and evidence.get(None, REV1) == verified
    # a later non-verified record never replaces it, and put returns the authoritative stored row
    bad.pub.fail_validate = True
    again = bad.verify(bad.good(), evidence=evidence)
    assert again == verified and evidence.get(None, REV1) == verified
    assert store.db.execute('SELECT COUNT(*) FROM program_evidence').fetchone()[0] == 1


def test_put_replaces_an_invalid_row(hw):
    first = hw.verify(hw.good())
    evidence = hw.verifier.evidence
    with hw.store.transaction() as db:
        db.execute("UPDATE program_evidence SET record='not json' WHERE revision_id=?", (REV1,))
    with pytest.raises(EvidenceRowInvalid):
        evidence.get(None, REV1)
    assert evidence.put(first) == first
    assert evidence.get(None, REV1) == first


def test_get_revalidates_the_record_and_the_columns(hw):
    record = hw.verify(hw.good())
    evidence = hw.verifier.evidence

    def tamper(sql, *args):
        with hw.store.transaction() as db:
            db.execute(sql, args)

    assert evidence.get(None, 'f' * 32) is None
    # edited record text: the verdict no longer follows from the checks
    tamper("UPDATE program_evidence SET record=replace(record, '\"verdict\":\"verified\"', "
           "'\"verdict\":\"failed\"') WHERE revision_id=?", REV1)
    with pytest.raises(EvidenceRowInvalid):
        evidence.get(None, REV1)
    with hw.store.transaction() as db:
        assert evidence.match_problem(db, REV1, job_id=JOB, bundle=hw.pub.bundle) == DELIVERED_EVIDENCE_STALE
    # restore, then break each column in turn
    tamper('DELETE FROM program_evidence WHERE revision_id=?', REV1)
    assert evidence.put(record) == record
    for column, value in (('job_id', JOB2), ('manifest_sha256', 'e' * 64), ('step_sha256', 'e' * 64),
                          ('check_plan_sha256', 'e' * 64), ('method_version', 'x'), ('verdict', 'failed')):
        tamper(f'UPDATE program_evidence SET {column}=? WHERE revision_id=?', value, REV1)
        with pytest.raises(EvidenceRowInvalid):
            evidence.get(None, REV1)
        with hw.store.transaction() as db:
            assert evidence.match_problem(db, REV1, job_id=JOB, bundle=hw.pub.bundle) == DELIVERED_EVIDENCE_STALE
        tamper('DELETE FROM program_evidence WHERE revision_id=?', REV1)
        evidence.put(record)
    # a row stored under another revision id than its record names
    tamper('UPDATE program_evidence SET revision_id=? WHERE revision_id=?', REV2, REV1)
    with pytest.raises(EvidenceRowInvalid):
        evidence.get(None, REV2)


def test_match_problem_stale_and_unverified_cases(hw, compiled, lock):
    hw.verify(hw.good())
    evidence = hw.verifier.evidence
    other = HostWorld(compiled, lock, job=JOB2, revision_id=REV2)
    other.resign(attempts=b'[]\n')  # a different manifest

    def problem(rev_id, **kw):
        with hw.store.transaction() as db:
            return evidence.match_problem(db, rev_id, **kw)

    assert problem(REV1, job_id=JOB, bundle=hw.pub.bundle) is None
    assert problem(REV2, job_id=JOB2, bundle=other.pub.bundle) == DELIVERED_UNVERIFIED  # no row
    assert problem(REV1, job_id=JOB2, bundle=hw.pub.bundle) == DELIVERED_EVIDENCE_STALE  # rev1 row, other job
    assert problem(REV1, job_id=JOB, bundle=other.pub.bundle) == DELIVERED_EVIDENCE_STALE  # other manifest
    assert problem(REV1, job_id=JOB, bundle=None) == DELIVERED_EVIDENCE_STALE
    assert problem(REV1, job_id=JOB, bundle=hw.pub.bundle, method_version='b' * 64) == DELIVERED_EVIDENCE_STALE
    # the manifest of a republished (re-signed) bundle differs from the row's
    hw.resign(attempts=b'[]\n')
    assert problem(REV1, job_id=JOB, bundle=hw.pub.bundle) == DELIVERED_EVIDENCE_STALE


def test_match_problem_stale_for_an_old_method_version_row(hw, monkeypatch):
    monkeypatch.setattr(dv, 'method_version', lambda: 'c' * 64)
    record = hw.verify(hw.good())
    assert record.verdict == 'verified' and record.method_version == 'c' * 64
    monkeypatch.undo()
    with hw.store.transaction() as db:
        assert hw.verifier.evidence.match_problem(db, REV1, job_id=JOB, bundle=hw.pub.bundle) == DELIVERED_EVIDENCE_STALE


def test_match_problem_maps_every_verdict_to_a_d7_reason(hw, compiled, lock):
    def row_problem(world, record):
        with world.store.transaction() as db:
            return world.verifier.evidence.match_problem(db, REV1, job_id=JOB, bundle=world.pub.bundle)

    failed = HostWorld(compiled, lock)
    failed.resign(report=b'{}\n')
    record = failed.verify(failed.good())
    assert record.verdict == 'failed' and row_problem(failed, record) == DELIVERED_CHECK_FAILED
    ambiguous = HostWorld(compiled, lock)
    record = ambiguous.verify(MeasureAdapter(face_table('root_face_duplicated', lock_versions(ambiguous.revision)),
                                             fixture_mesh()))
    assert record.verdict == 'ambiguous' and row_problem(ambiguous, record) == DELIVERED_BINDING_AMBIGUOUS
    mock = HostWorld(compiled, lock)
    record = mock.verify(adapter_for('occt', mode='mock'))
    assert record.verdict == 'unavailable' and row_problem(mock, record) == DELIVERED_EVIDENCE_UNAVAILABLE


# --------------------------------------------------------------------------- non-verified outcomes

def assert_unavailable(record, reason, adapter=None):
    assert record.verdict == 'unavailable' and record.reasons == (reason,)
    assert not any(c.state == 'passed' for c in record.checks)
    if adapter is not None:
        assert adapter.calls == []


def test_non_accepted_jobs_are_never_verified(hw):
    cases = {
        'rejected': hw.rejected_run(),
        'unavailable': hw.accepted_run(lifecycle='unavailable', outcome='not_evaluated', attempts=[],
                                       selected_candidate_sha256=None, bundle=None,
                                       failures=['Process interrupted']),
        'running': hw.accepted_run(lifecycle='running', outcome='not_evaluated', attempts=[],
                                   selected_candidate_sha256=None, bundle=None),
    }
    for name, run in cases.items():
        hw.write_run(run)
        adapter = hw.good()
        record = hw.verify(adapter, revision=unbundled(hw.revision))
        assert_unavailable(record, 'measurement_unavailable', adapter)
        assert hw.pub.validated == 0 and hw.pub.opened == 0, name
        assert record.manifest_sha256 == UNKNOWN_SHA256 and record.step_sha256 is None
        with hw.store.transaction() as db:
            assert hw.verifier.evidence.match_problem(
                db, REV1, job_id=JOB, bundle=hw.pub.bundle) == DELIVERED_EVIDENCE_UNAVAILABLE


def test_missing_jobs_row_job_mismatch_and_expired_run_are_unavailable(hw):
    adapter = hw.good()
    with hw.store.transaction() as db:
        db.execute('DELETE FROM jobs')
    assert_unavailable(hw.verify(adapter), 'measurement_unavailable', adapter)
    hw.write_run(hw.accepted_run(job_id=JOB2), job=JOB)  # row under the revision's job names another job
    assert_unavailable(hw.verify(adapter), 'measurement_unavailable', adapter)
    hw.write_run(hw.accepted_run())
    with hw.store.transaction() as db:
        db.execute('UPDATE jobs SET record=? WHERE id=?', ('{"schema_version": "engineering_expired_run_v1"}', JOB))
    assert_unavailable(hw.verify(adapter), 'measurement_unavailable', adapter)
    with hw.store.transaction() as db:
        db.execute('UPDATE jobs SET record=? WHERE id=?', ('not json', JOB))
    assert_unavailable(hw.verify(adapter), 'measurement_unavailable', adapter)
    assert adapter.calls == []


def test_revision_without_a_job_or_with_another_bundle_is_unavailable(hw):
    adapter = hw.good()
    staged = evolve(hw.revision, state='staged', job_id=None, brief_id=None, bundle=None, published_seq=None)
    assert_unavailable(hw.verify(adapter, revision=staged), 'measurement_unavailable', adapter)
    other = HostWorld(hw.compiled, hw.lock, job=JOB2)
    foreign = with_bundle(hw.revision, other.pub.bundle)
    assert_unavailable(hw.verify(adapter, revision=foreign), 'measurement_unavailable', adapter)


def test_publisher_validation_failure_is_unavailable(hw):
    adapter = hw.good()
    hw.pub.fail_validate = True
    assert_unavailable(hw.verify(adapter), 'measurement_unavailable', adapter)
    assert hw.pub.opened == 0


def test_plan_mismatch_is_unavailable_lock_mismatch(hw):
    adapter = hw.good()
    for field in ('brief_sha256', 'fixed_requirements_sha256', 'semantic_sha256'):
        record = hw.verify(adapter, revision=evolve(hw.revision, **{field: 'd' * 64}))
        assert_unavailable(record, 'lock_mismatch', adapter)
    with_other_lock = evolve(hw.revision, lock_sha256='d' * 64)
    assert_unavailable(hw.verify(adapter, revision=with_other_lock), 'lock_mismatch', adapter)
    # the run's own hashes must agree too
    hw.write_run(hw.accepted_run(brief_sha256='d' * 64, attempts=[], outcome='not_evaluated',
                                 lifecycle='unavailable', selected_candidate_sha256=None, bundle=None,
                                 failures=['x']))
    assert hw.verify(adapter).verdict == 'unavailable'
    assert adapter.calls == []


def test_payload_caps_and_identity_are_enforced(hw, monkeypatch):
    adapter = hw.good()
    small = tuple((fmt, name, 4) for fmt, name, _ in dv._PAYLOADS)
    monkeypatch.setattr(dv, '_PAYLOADS', small)
    assert_unavailable(hw.verify(adapter), 'measurement_unavailable', adapter)
    monkeypatch.undo()
    for fmt, name, cap in dv._PAYLOADS:  # each file in turn over its own cap
        capped = tuple((f, n, 4 if n == name else c) for f, n, c in dv._PAYLOADS)
        monkeypatch.setattr(dv, '_PAYLOADS', capped)
        assert_unavailable(hw.verify(adapter), 'measurement_unavailable', adapter)
        monkeypatch.undo()
    # bytes that differ from the manifest identity (same length)
    hw.pub.files['model.step'] = bytes(len(STEP))
    assert_unavailable(hw.verify(adapter), 'measurement_unavailable', adapter)
    assert hw.pub.opened == hw.pub.closed  # every lease closed, including on failure


def test_selected_spec_resigned_to_another_candidate_is_report_mismatch(hw):
    adapter = hw.good()
    other = hw.candidates[10.0].specification.model_dump(mode='json')
    hw.resign(spec=encode(other))
    record = hw.verify(adapter)
    assert record.verdict == 'failed' and record.reasons == ('report_mismatch',)
    assert record.candidate_sha256 == hw.selected.candidate_sha256  # the jobs row decides, never the file
    assert adapter.calls == []
    assert any(c.check_id == 'physics.report_inputs' and c.state == 'failed' for c in record.checks)
    assert not any(c.state == 'passed' for c in record.checks)


def test_resigned_attempts_are_report_mismatch(hw):
    adapter = hw.good()
    attempts = [a.model_dump(mode='json') for a in hw.attempts]
    cases = {
        'last_rejected': [dict(a) for a in attempts[:2]] + [dict(attempts[2], outcome='rejected')],
        'earlier_accepted': [dict(attempts[0], outcome='accepted')] + attempts[1:],
        'names_other_candidate': attempts[:2] + [dict(attempts[2], candidate=attempts[0]['candidate'])],
        'empty': [],
        'not_a_list': {'a': 1},
        'garbage_entries': ['x'],
    }
    for name, value in cases.items():
        hw.resign(attempts=encode(value))
        record = hw.verify(adapter)
        assert record.verdict == 'failed' and record.reasons == ('report_mismatch',), name
    assert adapter.calls == []


def test_resigned_report_is_report_mismatch(hw):
    adapter = hw.good()
    good = json.loads(json.dumps(hw.report))
    sel = hw.selected.candidate_sha256

    def edit(fn):
        report = json.loads(json.dumps(good))
        fn(report)
        return encode(report)

    def evaluation(report):
        return next(e for e in report['evaluations'] if e['candidate_sha256'] == sel)

    cases = {
        'brief': edit(lambda r: r.update(brief_sha256='d' * 64)),
        'fixed': edit(lambda r: r.update(fixed_requirements_sha256='d' * 64)),
        'no_evaluation': edit(lambda r: r.update(evaluations=[e for e in r['evaluations']
                                                              if e['candidate_sha256'] != sel])),
        'duplicate_evaluation': edit(lambda r: r['evaluations'].append(dict(evaluation(r)))),
        'physics_failed': edit(lambda r: evaluation(r)['native_report']['physics'].update(state='failed')),
        'no_physics': edit(lambda r: evaluation(r)['native_report'].pop('physics')),
        'no_measurements': edit(lambda r: evaluation(r)['native_report'].pop('measurements')),
        'string_input': edit(lambda r: evaluation(r)['native_report']['measurements'].update(length_mm='200')),
        'bool_input': edit(lambda r: evaluation(r)['native_report']['measurements'].update(height_mm=True)),
        'negative_input': edit(lambda r: evaluation(r)['native_report']['measurements'].update(section_iy_mm4=-1.0)),
        'not_json': b'{',
        'array': b'[]',
    }
    cases['nan'] = json.dumps(good).replace('"length_mm": 200.0', '"length_mm": NaN', 1).encode()
    assert b'NaN' in cases['nan']
    for name, data in cases.items():
        hw.resign(report=data)
        record = hw.verify(adapter)
        assert record.verdict == 'failed' and record.reasons == ('report_mismatch',), name
    assert adapter.calls == []


def test_report_physics_inputs_that_disagree_with_the_measured_geometry_fail(hw):
    adapter = hw.good()
    report = json.loads(json.dumps(hw.report))
    sel = hw.selected.candidate_sha256
    for e in report['evaluations']:
        if e['candidate_sha256'] == sel:
            e['native_report']['measurements']['section_iy_mm4'] *= 1.5
    hw.resign(report=encode(report))
    record = hw.verify(adapter)
    assert len(adapter.calls) == 1  # measured, then bound against the measured geometry
    assert record.verdict == 'failed' and 'report_mismatch' in record.reasons
    physics = next(c for c in record.checks if c.check_id == 'physics.report_inputs')
    assert physics.state == 'failed' and physics.coverage == 'policy_report'


def test_measure_timeout_error_and_empty_results_are_unavailable(hw):
    started = time.monotonic()
    slow = hw.good(delay=30)
    record = hw.verify(slow, timeout_s=0.05)
    assert time.monotonic() - started < 5
    assert_unavailable(record, 'measurement_unavailable')
    assert len(slow.calls) == 1
    for adapter in (hw.good(boom=RuntimeError('native crash')), hw.good(boom=OSError('gone')),
                    MeasureAdapter(None, None, reason='native_failed'),
                    MeasureAdapter(None, None, reason='too_many_faces')):
        assert_unavailable(hw.verify(adapter), 'measurement_unavailable')
    garbage = MeasureAdapter('not a table', None)
    assert_unavailable(hw.verify(garbage), 'measurement_unavailable')


def test_mock_adapter_and_adapters_without_measure_are_unavailable_mock_backend(hw):
    mock = adapter_for('occt', mode='mock')
    assert_unavailable(hw.verify(mock), 'mock_backend')

    class NoMeasure:
        mode = 'native'

    assert_unavailable(hw.verify(NoMeasure()), 'mock_backend')
    verifier = DeliveredVerifier(hw.store, hw.pub, lambda backend, mode='native': (_ for _ in ()).throw(
        RuntimeError('no adapter')))
    assert_unavailable(asyncio.run(verifier.verify(hw.revision, hw.ir)), 'measurement_unavailable')


def test_lock_native_version_mismatch_is_unavailable(hw):
    adapter = MeasureAdapter(face_table(versions=(('cadquery', '9.9.9'), ('OCP', '0.0.1'))), fixture_mesh())
    record = hw.verify(adapter)
    assert record.verdict == 'unavailable' and 'lock_mismatch' in record.reasons
    assert len(adapter.calls) == 1 and not any(c.state == 'passed' for c in record.checks)


def test_a_bad_or_empty_mesh_fails_closed_and_a_missing_mesh_is_not_a_pass(hw):
    empty = MeshSummary(triangle_count=0, vertex_count=0, bounds_mm=(0.0,) * 6, mesh_bound_mm=0.1)
    record = hw.verify(MeasureAdapter(face_table(versions=lock_versions(hw.revision)), empty,
                                      reason=None))
    assert record.verdict == 'failed'
    assert next(c for c in record.checks if c.check_id == 'delivered.preview_extent').state == 'failed'


def test_ambiguous_binding_gives_an_ambiguous_record(hw):
    adapter = MeasureAdapter(face_table('root_face_duplicated', lock_versions(hw.revision)), fixture_mesh())
    record = hw.verify(adapter)
    assert record.verdict == 'ambiguous'


def test_the_semaphore_serializes_measurements(hw):
    adapter = hw.good(delay=0.05)
    verifier = DeliveredVerifier(hw.store, hw.pub, factory(adapter))

    async def both():
        return await asyncio.gather(verifier.verify(hw.revision, hw.ir), verifier.verify(hw.revision, hw.ir))

    first, second = asyncio.run(both())
    assert adapter.peak == 1 and len(adapter.calls) == 2
    assert first.verdict == second.verdict == 'verified'


def test_verify_never_raises_for_a_broken_plan_input(hw):
    adapter = hw.good()
    # an IR that is not the beam brief cannot produce a plan: unavailable, nothing measured
    verifier = DeliveredVerifier(hw.store, hw.pub, factory(adapter))
    record = asyncio.run(verifier.verify(hw.revision, None))
    assert_unavailable(record, 'measurement_unavailable', adapter)
    assert record.check_plan_sha256 == UNKNOWN_SHA256 and record.checks == ()


def test_a_verified_record_can_never_carry_a_missing_or_placeholder_hash(hw):
    record = hw.verify(hw.good())
    assert record.verdict == 'verified'
    data = json.loads(record.model_dump_json())
    for field, value in (('step_sha256', None), ('step_sha256', UNKNOWN_SHA256),
                         ('manifest_sha256', UNKNOWN_SHA256), ('check_plan_sha256', UNKNOWN_SHA256),
                         ('candidate_sha256', UNKNOWN_SHA256)):
        with pytest.raises(ValueError):
            DeliveredEvidence.model_validate_json(json.dumps(dict(data, **{field: value})))


def test_non_verified_records_validate_and_round_trip(hw):
    hw.pub.fail_validate = True
    record = hw.verify(hw.good())
    assert DeliveredEvidence.model_validate_json(record.model_dump_json()) == record
    assert record.checks and record.step_sha256 is None
    verdict_free = [c for c in record.checks if c.category in ('manufacturing', 'standards')]
    assert all(c.state == 'not_evaluated' for c in verdict_free)


# --------------------------------------------------------------------------- posix: real publisher

def resign_bundle(store, publisher, job_id, replacements):
    """Rewrite published files of a real job and keep every record consistent so that
    publisher.validate(job_id, new_bundle) still passes: the files, manifest.json, package.zip, the
    publications row and the bundle in the jobs row. Returns the new BundleIdentity.

    replacements maps a published name (for example 'selected_spec.json') to its new bytes."""
    directory = publisher.directory(job_id)
    with store._lock:
        publication, _ = publisher._publication(job_id)
    old = {p['path'] for p in publication['payloads']}
    assert set(replacements) <= old
    files = {name: (directory / name).read_bytes() for name in old}
    old_manifest = json.loads((directory / 'manifest.json').read_bytes())
    replacements = dict(replacements)
    # The accepted run's bundle digests must equal the digests its last attempt's checks recorded for the
    # same paths (EngineeringRun.consistent_run), so a re-signer rewrites those checked-artifact identities
    # too: in the jobs row and, unless the caller supplies attempts.json itself, in the bundle's
    # attempts.json. Only the replaced paths are touched.
    resigned = {n: (digest(v), len(v)) for n, v in replacements.items() if n != 'attempts.json'}

    def resync(attempt):
        touched = False
        for check in attempt['checks']:
            for item in check['artifacts']:
                if item['path'] in resigned and (item['sha256'], item['bytes']) != (
                        resigned[item['path']][0], resigned[item['path']][1]):
                    item['sha256'], item['bytes'] = resigned[item['path']]
                    touched = True
        return touched

    if 'attempts.json' not in replacements:
        attempts_list = json.loads(files['attempts.json'])
        if resync(attempts_list[-1]):
            replacements['attempts.json'] = encode(attempts_list)
    files.update(replacements)
    identities =[ArtifactIdentity(path=n, bytes=len(v), sha256=digest(v)) for n, v in sorted(files.items())]
    manifest = dict(schema_version='engineering_manifest_v1', job_id=job_id,
                    brief_sha256=old_manifest['brief_sha256'],
                    selected_candidate_sha256=old_manifest['selected_candidate_sha256'],
                    kind=old_manifest['kind'],
                    payloads=[dict(i.model_dump(), mime_type=MIME[i.path]) for i in identities])
    manifest_data = encode(BundleManifest.model_validate_json(json.dumps(manifest)).model_dump(mode='json'))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, value in {**files, 'manifest.json': manifest_data}.items():
            z.writestr(name, value)
    zip_data = archive.getvalue()
    stored = sum(len(v) for v in files.values()) + len(manifest_data) + len(zip_data) + METADATA_RESERVE
    publication.update(
        payloads=[i.model_dump() for i in identities], manifest_sha256=digest(manifest_data),
        manifest_bytes=len(manifest_data), zip_sha256=digest(zip_data), zip_bytes=len(zip_data),
        stored_bytes=stored)
    bundle = BundleIdentity(payloads=tuple(identities), manifest_sha256=publication['manifest_sha256'],
                            manifest_bytes=publication['manifest_bytes'], zip_sha256=publication['zip_sha256'],
                            zip_bytes=publication['zip_bytes'], stored_bytes=stored)
    publication['bundle'] = bundle.model_dump(mode='json')
    with store.transaction() as db:
        row = db.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()
        run = EngineeringRun.model_validate_json(row[0])
        attempts = run.model_dump()['attempts']
        resync(attempts[-1])  # same rewrite as attempts.json, now in the jobs row
        new_run = changed(run, bundle=bundle, attempts=attempts)  # validates before anything is written
        for name, value in {**replacements, 'manifest.json': manifest_data, 'package.zip': zip_data}.items():
            (directory / name).write_bytes(value)
        db.execute('UPDATE publications SET record=? WHERE job=?', (json.dumps(publication), job_id))
        db.execute('UPDATE jobs SET record=? WHERE id=?', (new_run.model_dump_json(), job_id))
    publisher.validate(job_id, bundle)  # the tampered bundle is internally consistent
    return bundle


if os.name == 'posix':
    from engineering.language.semantic import compile_source
    from tests.test_program_revisions import (  # noqa: F401  (fixtures: native capture, OCCT adapter)
        BEAM, CLIENT, REJECTED, WHITESPACE, _replay, _publisher_class, evals, land, occt, step_sha,
    )

    class World:
        pass

    @asynccontextmanager
    async def vworld(tmp_path, evals, occt, *, gate=None):
        """The F06 posix world: real SQLiteStore, BundlePublisher, JobController on a native replay.

        gate is None: verifier=None (F06 behaviour, the verifier tests drive DeliveredVerifier directly).
        gate is an adapter factory taking the world: the service runs with the REAL delivered gate."""
        from engineering.briefs import BriefService
        from engineering.jobs import JobController
        from engineering.store import SQLiteBriefRepository, SQLiteStore
        w = World()
        w.store = SQLiteStore(tmp_path)
        w.pub = _publisher_class()(w.store)
        w.briefs = BriefService(SQLiteBriefRepository(w.store))
        w.replay = _replay(evals)
        w.jobs = JobController(w.store, publisher=w.pub, adapter=w.replay)
        w.measure = None
        verifier = None
        if gate is not None:
            w.measure = gate(w)
            verifier = DeliveredVerifier(w.store, w.pub, factory(w.measure), timeout_s=10)
        w.svc = RevisionService(w.store, w.jobs, w.pub, w.briefs,
                                adapter_factory=lambda backend, mode='native': occt, verifier=verifier)
        w.pid = w.svc.create_program(CLIENT)
        try:
            yield w
        finally:
            await w.svc.close()
            await w.jobs.close()
            w.store.close()

    def replay_adapter(rev, **kw):
        return MeasureAdapter(face_table(versions=lock_versions(rev)), fixture_mesh(), **kw)

    def run_world(tmp_path, evals, occt, body):
        async def scenario():
            async with vworld(tmp_path, evals, occt) as w:
                first = await land(w, BEAM, None)
                assert first.state == 'published', first
                await body(w, first, compile_source(BEAM.encode('utf-8'), source_id='beam.mir'))
        asyncio.run(scenario())

    def selected_specs(compiled_beam):
        return {c.specification.height_mm: c for c in _candidates_from_content(compiled_beam.brief_content)}

    @posix_only
    def test_posix_verified_record_for_the_15mm_run(tmp_path, evals, occt):
        async def body(w, rev, compiled_beam):
            adapter = replay_adapter(rev)
            verifier = DeliveredVerifier(w.store, w.pub, factory(adapter), timeout_s=10)
            record = await verifier.verify(rev, compiled_beam.ir)
            assert record.verdict == 'verified', record.reasons
            assert record.candidate_sha256 == selected_specs(compiled_beam)[15.0].candidate_sha256
            assert record.manifest_sha256 == rev.bundle.manifest_sha256
            (step, glb, _), = adapter.calls
            download = w.svc.open_artifact(rev.revision_id, 'step', client=CLIENT)
            try:
                assert step.data == download.file.read() and step.ref.sha256 == download.sha256 == record.step_sha256
            finally:
                download.close()
            assert not w.pub.leases.get(rev.job_id)  # every lease closed
            with w.store.transaction() as db:
                assert verifier.evidence.get(db, rev.revision_id) == record
                assert verifier.evidence.match_problem(db, rev.revision_id, job_id=rev.job_id, bundle=rev.bundle) is None
        run_world(tmp_path, evals, occt, body)

    @posix_only
    def test_posix_rejected_job_is_unavailable_and_measures_nothing(tmp_path, evals, occt):
        async def scenario():
            async with vworld(tmp_path, evals, occt) as w:
                rev = await land(w, REJECTED, None)
                assert rev.state == 'rejected' and rev.job_id
                adapter = replay_adapter(rev)
                verifier = DeliveredVerifier(w.store, w.pub, factory(adapter))
                record = await verifier.verify(rev, compile_source(REJECTED.encode('utf-8'), source_id='beam.mir').ir)
                assert record.verdict == 'unavailable' and adapter.calls == []
                assert not any(c.state == 'passed' for c in record.checks)
        asyncio.run(scenario())

    @posix_only
    def test_posix_selected_candidate_comes_from_the_jobs_row_not_selected_spec(tmp_path, evals, occt):
        async def body(w, rev, compiled_beam):
            other = selected_specs(compiled_beam)[10.0].specification.model_dump(mode='json')
            bundle = resign_bundle(w.store, w.pub, rev.job_id, {'selected_spec.json': encode(other)})
            adapter = replay_adapter(rev)
            verifier = DeliveredVerifier(w.store, w.pub, factory(adapter))
            record = await verifier.verify(with_bundle(rev, bundle), compiled_beam.ir)
            assert record.verdict == 'failed' and record.reasons == ('report_mismatch',)
            assert record.candidate_sha256 == selected_specs(compiled_beam)[15.0].candidate_sha256
            assert adapter.calls == []
        run_world(tmp_path, evals, occt, body)

    @posix_only
    def test_posix_resigned_report_physics_inputs_are_report_mismatch(tmp_path, evals, occt):
        async def body(w, rev, compiled_beam):
            directory = w.pub.directory(rev.job_id)
            sel = selected_specs(compiled_beam)[15.0].candidate_sha256
            report = json.loads((directory / 'report.json').read_bytes())
            evaluation = next(e for e in report['evaluations'] if e['candidate_sha256'] == sel)
            evaluation['native_report']['physics']['state'] = 'failed'
            bundle = resign_bundle(w.store, w.pub, rev.job_id, {'report.json': encode(report)})
            adapter = replay_adapter(rev)
            verifier = DeliveredVerifier(w.store, w.pub, factory(adapter))
            record = await verifier.verify(with_bundle(rev, bundle), compiled_beam.ir)
            assert record.verdict == 'failed' and record.reasons == ('report_mismatch',) and adapter.calls == []
            # inputs altered instead: measured, then caught against the delivered geometry
            evaluation['native_report']['physics']['state'] = 'passed'
            evaluation['native_report']['measurements']['section_iy_mm4'] *= 2.0
            bundle = resign_bundle(w.store, w.pub, rev.job_id, {'report.json': encode(report)})
            record = await verifier.verify(evolve(rev, bundle=bundle), compiled_beam.ir)
            assert record.verdict == 'failed' and 'report_mismatch' in record.reasons
        run_world(tmp_path, evals, occt, body)

    @posix_only
    def test_posix_resigned_attempts_are_report_mismatch(tmp_path, evals, occt):
        async def body(w, rev, compiled_beam):
            attempts = json.loads((w.pub.directory(rev.job_id) / 'attempts.json').read_bytes())
            attempts[-1]['outcome'] = 'rejected'
            bundle = resign_bundle(w.store, w.pub, rev.job_id, {'attempts.json': encode(attempts)})
            adapter = replay_adapter(rev)
            verifier = DeliveredVerifier(w.store, w.pub, factory(adapter))
            record = await verifier.verify(with_bundle(rev, bundle), compiled_beam.ir)
            assert record.verdict == 'failed' and record.reasons == ('report_mismatch',) and adapter.calls == []
        run_world(tmp_path, evals, occt, body)

    @posix_only
    def test_posix_timeout_mock_and_lock_mismatch_are_unavailable(tmp_path, evals, occt):
        async def body(w, rev, compiled_beam):
            ir = compiled_beam.ir
            started = time.monotonic()
            slow = replay_adapter(rev, delay=30)
            record = await DeliveredVerifier(w.store, w.pub, factory(slow), timeout_s=0.05).verify(rev, ir)
            assert record.verdict == 'unavailable' and time.monotonic() - started < 10
            mock = await DeliveredVerifier(w.store, w.pub, factory(adapter_for('occt', mode='mock'))).verify(rev, ir)
            assert mock.verdict == 'unavailable' and mock.reasons == ('mock_backend',)
            wrong = MeasureAdapter(face_table(versions=(('cadquery', '9.9.9'), ('OCP', '0.0.1'))), fixture_mesh())
            record = await DeliveredVerifier(w.store, w.pub, factory(wrong)).verify(rev, ir)
            assert record.verdict == 'unavailable' and 'lock_mismatch' in record.reasons
        run_world(tmp_path, evals, occt, body)

    @posix_only
    def test_posix_stale_when_the_republished_manifest_differs(tmp_path, evals, occt):
        async def body(w, rev, compiled_beam):
            adapter = replay_adapter(rev)
            verifier = DeliveredVerifier(w.store, w.pub, factory(adapter))
            record = await verifier.verify(rev, compiled_beam.ir)
            assert record.verdict == 'verified'
            attempts = (w.pub.directory(rev.job_id) / 'attempts.json').read_bytes()
            bundle = resign_bundle(w.store, w.pub, rev.job_id, {'attempts.json': attempts + b'\n'})
            assert bundle.manifest_sha256 != rev.bundle.manifest_sha256
            with w.store.transaction() as db:
                ev = verifier.evidence
                assert ev.match_problem(db, rev.revision_id, job_id=rev.job_id, bundle=bundle) == DELIVERED_EVIDENCE_STALE
                assert ev.match_problem(db, rev.revision_id, job_id=rev.job_id, bundle=rev.bundle) is None
                assert ev.match_problem(db, REV2, job_id=rev.job_id, bundle=rev.bundle) == DELIVERED_UNVERIFIED
        run_world(tmp_path, evals, occt, body)

    class LazyReplay(MeasureAdapter):
        """Replay whose face table carries the native versions of the world's own lock (known after staging)."""

        def __init__(self, world, name='ideal_box_h15'):
            super().__init__()
            self.world, self.name = world, name

        async def measure(self, step, glb, plan):
            rev = self.world.svc.history(self.world.pid, client=CLIENT)[0]
            self.table = face_table(self.name, lock_versions(rev))
            # a fixture without its own mesh (null) is replayed with the ideal h15 mesh, exactly like
            # test_failed_or_ambiguous_verdict_withholds_promotion: the failure must come from the geometry
            self.mesh = fixture_mesh(self.name if fixture_case(self.name)['mesh'] is not None else 'ideal_box_h15')
            return await super().measure(step, glb, plan)

    @posix_only
    def test_posix_real_gate_publishes_a_verified_run_and_withholds_a_failed_one(tmp_path, evals, occt):
        async def scenario():
            async with vworld(tmp_path, evals, occt, gate=LazyReplay) as w:
                first = await land(w, BEAM, None)
                assert first.state == 'published', (first, w.svc.last_settle_error)
                stored = w.svc.evidence(first.revision_id, client=CLIENT)
                assert stored.verdict == 'verified'
                entries = [e for e in w.svc.journal(w.pid, client=CLIENT)
                           if e.event == 'published' and e.revision_id == first.revision_id]
                assert len(entries) == 1
                assert entries[0].detail == {'job_id': first.job_id, 'evidence_hash': evidence_sha256(stored)}
                sha = step_sha(w, first.revision_id)
                with pytest.raises(KeyError):
                    w.svc.evidence(first.revision_id, client='someone-else')
                w.measure.name = 'box_h10_for_candidate_h15'
                second = await land(w, WHITESPACE, first.revision_id)
                assert second.state == 'failed' and second.reason == DELIVERED_CHECK_FAILED
                view = w.svc.current(w.pid, client=CLIENT)
                assert view.revision.revision_id == first.revision_id and view.artifacts == 'available'
                assert step_sha(w, first.revision_id) == sha
                assert w.svc.evidence(second.revision_id, client=CLIENT).verdict == 'failed'
        asyncio.run(scenario())

    @posix_only
    def test_posix_real_gate_never_verifies_a_rejected_job(tmp_path, evals, occt):
        async def scenario():
            async with vworld(tmp_path, evals, occt, gate=LazyReplay) as w:
                rev = await land(w, REJECTED, None)
                assert rev.state == 'rejected' and rev.reason == 'rejected_by_policy'
                assert w.measure.calls == []
                assert w.svc.evidence(rev.revision_id, client=CLIENT) is None
                assert w.svc.current(w.pid, client=CLIENT) is None
        asyncio.run(scenario())


# --------------------------------------------------------------------------- F07 task 4: RevisionService gate (host)

CH = 'client-h'
OLD_METHOD = 'e' * 64


class GatePublisher(FakePublisher):
    """FakePublisher plus what settle and restore ask of a real publisher."""

    def __init__(self, files, bundle, clock):
        super().__init__(files, bundle)
        self.clock = clock

    def quick_check(self, job_id, envelope=None):
        if envelope != self.bundle:
            raise ValueError('Package fingerprint changed')

    def expires_at(self, job_id):
        return self.clock() + 100.0

    @contextmanager
    def lease(self, job_id, fmt, *, envelope=None):
        yield


class CountingVerifier(DeliveredVerifier):
    calls = 0

    async def verify(self, revision, ir):
        self.calls += 1
        return await super().verify(revision, ir)


class StubVerifier:
    """A verifier that writes nothing (rows are planted by the test) or a canned record, and counts calls."""

    def __init__(self, store, record=None, boom=None):
        self.evidence, self.record, self.boom, self.calls = EvidenceStore(store), record, boom, 0

    async def verify(self, revision, ir):
        self.calls += 1
        if self.boom is not None:
            raise self.boom
        return self.evidence.put(self.record) if self.record is not None else None


async def _no_wait(job_id):
    return None


def gate_world(hw, clock, *, adapter=None, timeout_s=5.0):
    hw.pub = GatePublisher(hw.pub.files, hw.pub.bundle, clock)
    hw.adapter = adapter if adapter is not None else hw.good()
    hw.verifier = CountingVerifier(hw.store, hw.pub, factory(hw.adapter), timeout_s=timeout_s)
    return hw


def gate_service(hw, clock, verifier, **options):
    jobs = SimpleNamespace(store=hw.store, publisher=hw.pub, wait=_no_wait)
    briefs = SimpleNamespace(repository=SimpleNamespace(store=hw.store))
    return RevisionService(hw.store, jobs, hw.pub, briefs, clock=clock, verifier=verifier, **options)


def insert_running(hw, svc, pid, rid=REV1):
    """A running revision linked to the world's job, with its reviewed IR as the payload."""
    rev = evolve(hw.revision, revision_id=rid, evidence_revision_id=rid, program_id=pid, parent=None,
                 state='running', bundle=None, published_seq=None, reason=None, job_id=hw.job)
    payload = build_payload('beam.mir', 'x', hw.ir, hw.lock).model_dump_json()
    with hw.store.transaction() as db:
        svc.revision_store._insert(db, rev, payload, 1000.0)
    return rev


def state_of(svc, rid):
    """The stored revision record (the host jobs table has no expires column, so not view())."""
    with svc.store.transaction() as db:
        return svc.revision_store.get(db, rid)[0]


def settled(svc, rid, job=JOB):
    asyncio.run(svc._settle_when_done(rid, job))
    return state_of(svc, rid)


def head_of(hw):
    return hw.store.db.execute('SELECT head FROM programs').fetchone()[0]


def evidence_rows(hw):
    return hw.store.db.execute('SELECT COUNT(*) FROM program_evidence').fetchone()[0]


def event_names(svc, pid):
    return [e.event for e in svc.journal(pid, client=CH)]


def gated(hw, clock, **kw):
    gate_world(hw, clock, **kw)
    svc = gate_service(hw, clock, hw.verifier)
    pid = svc.create_program(CH)
    insert_running(hw, svc, pid)
    return svc, pid


def test_default_constructor_builds_the_real_verifier_and_none_disables_the_gate(hw, clock):
    gate_world(hw, clock)
    jobs = SimpleNamespace(store=hw.store, publisher=hw.pub, wait=_no_wait)
    briefs = SimpleNamespace(repository=SimpleNamespace(store=hw.store))
    default = RevisionService(hw.store, jobs, hw.pub, briefs, clock=clock)
    assert type(default.verifier) is DeliveredVerifier
    assert default.verifier.store is hw.store and default.verifier.publisher is hw.pub
    assert default.verifier.adapter_factory is default.adapter_factory
    parameter = inspect.signature(RevisionService.__init__).parameters['verifier']
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    off = RevisionService(hw.store, jobs, hw.pub, briefs, clock=clock, verifier=None)
    assert off.verifier is None
    pid = off.create_program(CH)
    insert_running(hw, off, pid)
    assert settled(off, REV1).state == 'published', off.last_settle_error  # F06 behaviour: no verification, no evidence row
    assert evidence_rows(hw) == 0 and off.evidence(REV1, client=CH) is None
    detail = next(e.detail for e in off.journal(pid, client=CH) if e.event == 'published')
    assert detail == {'job_id': JOB}


def test_verified_run_is_published_with_the_evidence_hash(hw, clock):
    svc, pid = gated(hw, clock)
    rev = settled(svc, REV1)
    assert rev.state == 'published' and rev.bundle == hw.pub.bundle and head_of(hw) == REV1
    stored = hw.verifier.evidence.get(None, REV1)
    assert stored.verdict == 'verified'
    entries = [e for e in svc.journal(pid, client=CH) if e.event == 'published']
    assert len(entries) == 1
    assert entries[0].detail == {'job_id': JOB, 'evidence_hash': evidence_sha256(stored)}
    assert svc.evidence(REV1, client=CH) == stored
    assert hw.verifier.calls == 1 and len(hw.adapter.calls) == 1
    assert svc.last_settle_error is None


def test_evidence_read_is_client_checked_like_view(hw, clock):
    svc, _pid = gated(hw, clock)
    settled(svc, REV1)
    for call in (lambda: svc.view(REV1, client='other'), lambda: svc.evidence(REV1, client='other'),
                 lambda: svc.evidence(REV2, client=CH), lambda: svc.evidence(None, client=CH)):
        with pytest.raises(KeyError):
            call()


def test_evidence_read_revalidates_the_stored_row(hw, clock):
    svc, _pid = gated(hw, clock)
    settled(svc, REV1)
    hw.store.db.execute("UPDATE program_evidence SET record='{}' WHERE revision_id=?", (REV1,))
    with pytest.raises(RuntimeError):
        svc.evidence(REV1, client=CH)


@pytest.mark.parametrize('table,reason', [
    ('box_h10_for_candidate_h15', DELIVERED_CHECK_FAILED),
    ('root_face_duplicated', DELIVERED_BINDING_AMBIGUOUS),
])
def test_failed_or_ambiguous_verdict_withholds_promotion(hw, clock, table, reason):
    adapter = MeasureAdapter(face_table(table, lock_versions(hw.revision)), fixture_mesh())
    svc, pid = gated(hw, clock, adapter=adapter)
    rev = settled(svc, REV1)
    assert rev.state == 'failed' and rev.reason == reason
    assert head_of(hw) is None and rev.bundle is None
    assert 'published' not in event_names(svc, pid) and 'rejected' not in event_names(svc, pid)
    assert hw.verifier.evidence.get(None, REV1).verdict != 'verified'


def test_measure_timeout_is_evidence_unavailable(hw, clock):
    svc, pid = gated(hw, clock, adapter=hw.good(delay=30), timeout_s=0.05)
    rev = settled(svc, REV1)
    assert rev.state == 'failed' and rev.reason == DELIVERED_EVIDENCE_UNAVAILABLE and head_of(hw) is None


def test_a_verifier_that_raises_leaves_no_row_and_fails_unverified(hw, clock):
    boom = StubVerifier(hw.store, boom=RuntimeError('verifier exploded'))
    gate_world(hw, clock)
    svc = gate_service(hw, clock, boom)
    pid = svc.create_program(CH)
    insert_running(hw, svc, pid)
    rev = settled(svc, REV1)
    assert rev.state == 'failed' and rev.reason == DELIVERED_UNVERIFIED and head_of(hw) is None
    assert boom.calls == 1 and evidence_rows(hw) == 0
    assert isinstance(svc.last_settle_error, RuntimeError)


def test_rejected_job_is_never_verified_and_stays_rejected(hw, clock):
    hw.write_run(hw.rejected_run())
    svc, pid = gated(hw, clock)
    rev = settled(svc, REV1)
    assert rev.state == 'rejected' and rev.reason == 'rejected_by_policy' and head_of(hw) is None
    assert hw.verifier.calls == 0 and hw.adapter.calls == [] and evidence_rows(hw) == 0
    assert svc.last_settle_error is None


def test_a_cancel_request_skips_verification(hw, clock):
    svc, pid = gated(hw, clock)
    hw.store.db.execute('UPDATE program_revisions SET cancel=1 WHERE id=?', (REV1,))
    rev = settled(svc, REV1)
    assert rev.state == 'cancelled' and hw.verifier.calls == 0 and evidence_rows(hw) == 0


def test_a_compacted_payload_skips_verification_and_fails_unverified(hw, clock):
    svc, pid = gated(hw, clock)
    hw.store.db.execute('UPDATE program_revisions SET payload=NULL WHERE id=?', (REV1,))
    rev = settled(svc, REV1)
    assert rev.state == 'failed' and rev.reason == DELIVERED_UNVERIFIED
    assert hw.verifier.calls == 0 and head_of(hw) is None


def test_durable_archive_failure_cannot_publish_a_verified_revision(hw, clock):
    gate_world(hw, clock)

    def unavailable(*args):
        assert head_of(hw) is None
        raise OSError('archive storage unavailable')

    svc = gate_service(hw, clock, hw.verifier,
                       publication_archive=SimpleNamespace(save=unavailable, discard=lambda rev: None))
    pid = svc.create_program(CH)
    insert_running(hw, svc, pid)
    result = settled(svc, REV1)
    assert result.state == 'failed' and result.reason == 'archive_unavailable'
    assert evidence_rows(hw) == 1
    assert head_of(hw) is None
    assert 'published' not in event_names(svc, pid)


def test_durable_archive_and_head_recover_after_interrupted_commit(hw, clock, monkeypatch):
    gate_world(hw, clock)
    saved = []
    archive = SimpleNamespace(save=lambda revision, *_: saved.append(revision), discard=lambda rev: None)
    svc = gate_service(hw, clock, hw.verifier, publication_archive=archive)
    pid = svc.create_program(CH)
    insert_running(hw, svc, pid)
    assert asyncio.run(hw.verifier.verify(state_of(svc, REV1), hw.ir)).verdict == 'verified'

    def interrupt(point):
        assert point == 'before_settle_commit'
        raise RuntimeError('interrupted commit')

    with monkeypatch.context() as scope:
        scope.setattr(svc, '_crash_point', interrupt)
        with pytest.raises(RuntimeError, match='interrupted commit'):
            svc.settle(REV1)
    assert head_of(hw) is None
    assert state_of(svc, REV1).state == 'running'
    assert len(saved) == 1 and saved[0].state == 'published'

    published = svc.settle(REV1, recovered=True)
    assert len(saved) == 2 and published == saved[-1]
    assert head_of(hw) == REV1
    assert event_names(svc, pid) == ['published', 'recovered']


def test_verification_cannot_accept_a_rejection(hw, clock):
    gate_world(hw, clock)
    verified = asyncio.run(hw.verifier.verify(hw.revision, hw.ir))
    assert verified.verdict == 'verified'
    hw.write_run(hw.rejected_run())
    always = StubVerifier(hw.store, record=verified)
    svc = gate_service(hw, clock, always)
    pid = svc.create_program(CH)
    insert_running(hw, svc, pid)
    rev = settled(svc, REV1)
    assert rev.state == 'rejected' and head_of(hw) is None
    assert always.calls == 0 and 'published' not in event_names(svc, pid)


def _copy_row(hw, source, target):
    hw.store.db.execute(
        'INSERT INTO program_evidence(revision_id, job_id, manifest_sha256, step_sha256, check_plan_sha256, '
        'method_version, verdict, record, created) SELECT ?, job_id, manifest_sha256, step_sha256, '
        'check_plan_sha256, method_version, verdict, record, created FROM program_evidence WHERE revision_id=?',
        (target, source))


def _planted_service(hw, clock):
    """A service whose verifier writes nothing: the test plants the evidence row it wants settle to see."""
    gate_world(hw, clock)
    stub = StubVerifier(hw.store)
    svc = gate_service(hw, clock, stub)
    return svc, svc.create_program(CH), stub


def test_stale_a_verified_row_copied_under_another_revision(hw, clock):
    gate_world(hw, clock)
    assert asyncio.run(hw.verifier.verify(hw.revision, hw.ir)).verdict == 'verified'
    _copy_row(hw, REV1, REV2)
    svc, pid, stub = _planted_service(hw, clock)
    insert_running(hw, svc, pid, rid=REV2)
    rev = settled(svc, REV2)
    assert rev.state == 'failed' and rev.reason == DELIVERED_EVIDENCE_STALE and head_of(hw) is None


def test_stale_a_row_made_by_an_older_method_version(hw, clock, monkeypatch):
    gate_world(hw, clock)
    monkeypatch.setattr(dv, 'method_version', lambda: OLD_METHOD)
    record = asyncio.run(hw.verifier.verify(hw.revision, hw.ir))
    assert record.verdict == 'verified' and record.method_version == OLD_METHOD
    monkeypatch.undo()
    assert method_version() != OLD_METHOD
    svc, pid, _stub = _planted_service(hw, clock)
    insert_running(hw, svc, pid)
    rev = settled(svc, REV1)
    assert rev.state == 'failed' and rev.reason == DELIVERED_EVIDENCE_STALE and head_of(hw) is None


def test_stale_a_row_whose_manifest_differs_from_the_run_bundle(hw, clock):
    gate_world(hw, clock)
    assert asyncio.run(hw.verifier.verify(hw.revision, hw.ir)).verdict == 'verified'
    old_manifest = hw.pub.bundle.manifest_sha256
    hw.resign(**{'attempts.json': hw.pub.files['attempts.json'] + b'\n'})  # republished after verification
    assert hw.pub.bundle.manifest_sha256 != old_manifest
    svc, pid, _stub = _planted_service(hw, clock)
    insert_running(hw, svc, pid)
    rev = settled(svc, REV1)
    assert rev.state == 'failed' and rev.reason == DELIVERED_EVIDENCE_STALE and head_of(hw) is None


def test_stale_a_tampered_row_record(hw, clock):
    gate_world(hw, clock)
    assert asyncio.run(hw.verifier.verify(hw.revision, hw.ir)).verdict == 'verified'
    hw.store.db.execute("UPDATE program_evidence SET record='{}' WHERE revision_id=?", (REV1,))
    svc, pid, _stub = _planted_service(hw, clock)
    insert_running(hw, svc, pid)
    assert settled(svc, REV1).reason == DELIVERED_EVIDENCE_STALE


def test_recover_without_a_row_fails_unverified_and_never_measures(hw, clock):
    gate_world(hw, clock)
    base = gate_service(hw, clock, None)  # creates the revision tables; its own recover has nothing to do
    pid = base.create_program(CH)
    insert_running(hw, base, pid)
    svc = gate_service(hw, clock, hw.verifier)  # the constructor recovers the running revision
    rev = state_of(svc, REV1)
    assert rev.state == 'failed' and rev.reason == DELIVERED_UNVERIFIED and head_of(hw) is None
    assert hw.verifier.calls == 0 and hw.adapter.calls == [] and evidence_rows(hw) == 0
    assert event_names(svc, pid)[-2:] == ['failed', 'recovered']


def test_recover_with_a_matching_verified_row_rolls_forward(hw, clock):
    gate_world(hw, clock)
    base = gate_service(hw, clock, None)
    pid = base.create_program(CH)
    insert_running(hw, base, pid)
    stored = asyncio.run(hw.verifier.verify(hw.revision, hw.ir))
    assert stored.verdict == 'verified' and hw.verifier.calls == 1
    svc = gate_service(hw, clock, hw.verifier)
    rev = state_of(svc, REV1)
    assert rev.state == 'published' and head_of(hw) == REV1
    assert hw.verifier.calls == 1  # recover measured nothing
    detail = next(e.detail for e in svc.journal(pid, client=CH) if e.event == 'published')
    assert detail['evidence_hash'] == evidence_sha256(stored)


def move_head_to_another_revision(hw, svc, pid):
    """REV2: a later published revision that is the program head (a restore needs a head to roll back from)."""
    other = evolve(hw.revision, revision_id=REV2, evidence_revision_id=REV2, program_id=pid, parent=REV1,
                   state='published', bundle=hw.pub.bundle, published_seq=2, reason=None, job_id=JOB2)
    with hw.store.transaction() as db:
        svc.revision_store._insert(db, other, None, 1000.0)
        db.execute('UPDATE programs SET head=?, head_seq=2 WHERE id=?', (REV2, pid))


def _published_target(hw, clock, *, verifier='gate'):
    """A published, restorable revision (publication row present) that is not the head."""
    gate_world(hw, clock)
    svc = gate_service(hw, clock, hw.verifier if verifier == 'gate' else None)
    pid = svc.create_program(CH)
    insert_running(hw, svc, pid)
    assert settled(svc, REV1).state == 'published'
    hw.store.db.execute('INSERT INTO publications(job, record, expires) VALUES (?,?,?)', (JOB, '{}', clock() + 1000.0))
    move_head_to_another_revision(hw, svc, pid)
    return svc, pid


def _store_counts(hw):
    return tuple(hw.store.db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                 for t in ('program_revisions', 'program_journal', 'program_evidence'))


def test_restore_with_a_verified_matching_row_succeeds_without_new_evidence(hw, clock):
    svc, pid = _published_target(hw, clock)
    before = _store_counts(hw)
    restored = asyncio.run(svc.restore(REV1, expected_head=REV2, client=CH))
    assert isinstance(restored, ProgramRevision) and restored.kind == 'restore' and restored.state == 'published'
    assert restored.evidence_revision_id == REV1 and head_of(hw) == restored.revision_id
    assert _store_counts(hw) == (before[0] + 1, before[1] + 1, before[2])  # no new evidence row
    assert hw.verifier.calls == 1 and len(hw.adapter.calls) == 1  # restore never re-measures
    assert svc.evidence(restored.revision_id, client=CH) == svc.evidence(REV1, client=CH) is not None


def test_restore_accepts_a_row_made_by_an_older_method_version(hw, clock, monkeypatch):
    monkeypatch.setattr(dv, 'method_version', lambda: OLD_METHOD)
    svc, pid = _published_target(hw, clock)  # published under the old method version
    monkeypatch.undo()
    assert svc.evidence(REV1, client=CH).method_version == OLD_METHOD
    restored = asyncio.run(svc.restore(REV1, expected_head=REV2, client=CH))
    assert isinstance(restored, ProgramRevision) and restored.state == 'published'


def test_restore_without_the_gate_needs_no_row(hw, clock):
    svc, pid = _published_target(hw, clock, verifier=None)
    assert evidence_rows(hw) == 0
    restored = asyncio.run(svc.restore(REV1, expected_head=REV2, client=CH))
    assert isinstance(restored, ProgramRevision) and restored.state == 'published'


def _damage_deleted(hw):
    hw.store.db.execute('DELETE FROM program_evidence')


def _damage_garbled(hw):
    hw.store.db.execute("UPDATE program_evidence SET record='{}'")


def _damage_columns(hw):
    hw.store.db.execute("UPDATE program_evidence SET job_id=?", (JOB2,))


def _damage_other_manifest(hw):
    """A valid verified row, but for a different bundle than the one the target published."""
    record = hw.verifier.evidence.get(None, REV1)
    other = record.model_copy(update={'manifest_sha256': 'f' * 64})
    text = dv.canonical_json(other)
    hw.store.db.execute('UPDATE program_evidence SET manifest_sha256=?, record=?', ('f' * 64, text))


@pytest.mark.parametrize('damage', [_damage_deleted, _damage_garbled, _damage_columns, _damage_other_manifest])
def test_restore_refuses_record_altered_when_the_evidence_row_is_missing_or_wrong(hw, clock, damage):
    svc, pid = _published_target(hw, clock)
    damage(hw)
    before = _store_counts(hw)
    refusal = asyncio.run(svc.restore(REV1, expected_head=REV2, client=CH))
    assert refusal == RevisionRefusal(('record_altered',))
    assert _store_counts(hw) == before and head_of(hw) == REV2


def test_restore_rechecks_the_evidence_inside_the_final_transaction(hw, clock):
    svc, pid = _published_target(hw, clock)
    before = _store_counts(hw)

    def crash_point(name):
        if name == 'restore_after_validate':
            _damage_deleted(hw)  # the row vanishes after pre-validation, before the final transaction

    svc._crash_point = crash_point
    refusal = asyncio.run(svc.restore(REV1, expected_head=REV2, client=CH))
    assert refusal == RevisionRefusal(('record_altered',))
    assert _store_counts(hw) == (before[0], before[1], 0) and head_of(hw) == REV2

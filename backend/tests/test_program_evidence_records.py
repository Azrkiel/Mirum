"""F07 task 1a: pure evidence records, check-plan builder, validators, versions and schemas."""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from engineering.capabilities import CAPABILITY
from engineering.language.semantic import compile_source
from engineering.program import evidence as ev
from engineering.program.errors import ProgramError
from engineering.program.evidence import (
    BEAM_CHECK_IDS, CandidateChecks, CheckEvidence, CheckLimit, CheckPlan, DeliveredEvidence,
    ErrorBound, FaceRecord, FaceSignature, FaceTable, FeatureBinding, Measurement, MeshSummary,
    build_check_plan, check_evidence_against_plan, check_plan_sha256, derive_verdict, evidence_sha256,
)
from engineering.program.hashing import execution_lock_v0_2_sha256
from engineering.program.ir import execution_lock_v0_2
from engineering.program.kernel import NativeVersion, ProbeResult, backend_lock, make_description
from engineering.program.planning import _candidates_from_content
from engineering.program.schema_export import SCHEMA_DIR, SCHEMA_MODELS, check
from engineering.program.versions import CHECK_PLAN_VERSION, DELIVERED_EVIDENCE_VERSION

ROOT = Path(__file__).resolve().parents[2]
P03 = Path(__file__).resolve().parent / 'fixtures' / 'program' / 'p03'
H = 'a' * 64
RID = '1' * 32
JID = '2' * 32
POL = CAPABILITY.numerical


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
def candidates(compiled):
    return _candidates_from_content(compiled.brief_content)


@pytest.fixture(scope='module')
def plan(compiled, lock, candidates):
    return build_check_plan(compiled.ir, lock, candidates, mesh_convention_pinned=True)


def entries_of(plan_, i=0):
    return {e.check_id: e for e in plan_.candidates[i].entries}


# ---- versions and schemas ----

def test_versions_and_schema_models():
    assert CHECK_PLAN_VERSION == 'mirum_check_plan_v0_1'
    assert DELIVERED_EVIDENCE_VERSION == 'mirum_delivered_evidence_v0_1'
    assert SCHEMA_MODELS['mirum_check_plan_v0_1.schema.json'] is CheckPlan
    assert SCHEMA_MODELS['mirum_delivered_evidence_v0_1.schema.json'] is DeliveredEvidence
    assert check() == []
    for name in ('mirum_check_plan_v0_1.schema.json', 'mirum_delivered_evidence_v0_1.schema.json'):
        assert json.loads((SCHEMA_DIR / name).read_text(encoding='utf-8'))


# ---- the plan ----

def test_builder_takes_no_measurement_argument():
    params = list(inspect.signature(build_check_plan).parameters)
    assert params == ['ir', 'lock', 'candidates', 'mesh_convention_pinned']


def test_plan_is_deterministic_and_order_independent(compiled, lock, candidates, plan):
    again = build_check_plan(compiled.ir, lock, candidates, mesh_convention_pinned=True)
    assert check_plan_sha256(again) == check_plan_sha256(plan)
    rev = build_check_plan(compiled.ir, lock, tuple(reversed(candidates)), mesh_convention_pinned=True)
    assert check_plan_sha256(rev) == check_plan_sha256(plan)
    assert check_plan_sha256(plan) != check_plan_sha256(
        build_check_plan(compiled.ir, lock, candidates, mesh_convention_pinned=False))
    assert plan.lock_sha256 == execution_lock_v0_2_sha256(lock)


def test_one_entry_set_per_reviewed_candidate(plan, candidates):
    assert len(candidates) == 3
    heights = sorted(c.specification.height_mm for c in candidates)
    assert heights == [5.0, 10.0, 15.0]
    assert {c.candidate_sha256 for c in plan.candidates} == {c.candidate_sha256 for c in candidates}
    for cand in plan.candidates:
        assert tuple(sorted(e.check_id for e in cand.entries)) == tuple(sorted(BEAM_CHECK_IDS))


def test_more_than_four_candidates_rejected(plan, compiled, lock, candidates):
    with pytest.raises(ProgramError):
        build_check_plan(compiled.ir, lock, candidates * 2, mesh_convention_pinned=True)
    base = plan.candidates[0]
    five = tuple(CandidateChecks(candidate_sha256=f'{i}' * 64, entries=base.entries) for i in range(5))
    data = plan.model_dump()
    data['candidates'] = five
    with pytest.raises(ValidationError):
        CheckPlan(**{**data, 'candidates': five})
    ok = plan.model_dump()
    ok['candidates'] = tuple(CandidateChecks(candidate_sha256=f'{i}' * 64, entries=base.entries)
                             for i in range(4))
    assert len(CheckPlan.model_validate(ok).candidates) == 4


def test_tolerances_come_from_reviewed_dimensions(plan, candidates):
    for i, cand in enumerate(plan.candidates):
        spec = next(c.specification for c in candidates if c.candidate_sha256 == cand.candidate_sha256)
        entries = {e.check_id: e for e in cand.entries}
        vol = {lim.name: lim for lim in entries['delivered.volume'].limits}['volume']
        want = max(POL.metric_absolute_error,
                   POL.metric_relative_error * (spec.length_mm * spec.width_mm * spec.height_mm))
        assert vol.value == want and vol.unit == 'mm3'
        assert vol.source == 'policy:metric_absolute_error'
        assert vol.scale_refs == ('reviewed:beam.length', 'reviewed:beam.width', 'reviewed:beam.height')
        area = {lim.name: lim for lim in entries['delivered.root_section'].limits}['area']
        assert area.value == max(POL.metric_absolute_error,
                                 POL.metric_relative_error * (spec.width_mm * spec.height_mm))
        dim = {lim.name: lim for lim in entries['delivered.length'].limits}['dimension']
        assert dim.value == spec.dimensional_tolerance_mm
        assert dim.source == 'reviewed:beam.dimensional_tolerance'


def test_every_limit_source_is_reviewed_or_policy(plan):
    for cand in plan.candidates:
        for e in cand.entries:
            for lim in e.limits:
                assert lim.source.startswith(('reviewed:', 'policy:'))


def test_preview_required_only_when_convention_pinned(compiled, lock, candidates, plan):
    # amended: required only when pinned AND the conservative rule can pass; 0.01 mm < deflection 0.1 mm
    assert entries_of(plan)['delivered.preview_extent'].required is False
    assert ev.preview_can_pass(0.01, (200.0, 10.0, 15.0)) is False
    assert ev.preview_can_pass(1.0, (200.0, 10.0, 15.0)) is True
    off = build_check_plan(compiled.ir, lock, candidates, mesh_convention_pinned=False)
    prev = entries_of(off)['delivered.preview_extent']
    assert prev.required is False and prev.required_representation == 'mesh'
    assert off.mesh_convention_pinned is False
    for cid in ('manufacturing.not_evaluated', 'standards.not_evaluated'):
        assert entries_of(plan)[cid].required is False


def test_builder_refuses_disagreeing_candidate(compiled, lock, candidates):
    spec = candidates[0].specification
    bad_spec = spec.model_copy(update={'length_mm': spec.length_mm + 1.0})
    bad = candidates[0].model_copy(update={'specification': bad_spec})
    with pytest.raises(ProgramError):
        build_check_plan(compiled.ir, lock, (bad,), mesh_convention_pinned=True)


@pytest.mark.parametrize('source', ['measured:volume', 'report:x', 'reviewed:unknown.id',
                                    'policy:not_a_field', 'reviewed:material.reference', 'policy:version',
                                    'x', ''])
def test_limit_rejects_unreviewed_sources(source):
    with pytest.raises(ValidationError):
        CheckLimit(name='x', value=1.0, unit='mm', comparison='le', source=source)


def test_limit_policy_and_scale_rules():
    ok = CheckLimit(name='n', value=POL.datum_budget_mm, unit='mm', comparison='abs_le',
                    source='policy:datum_budget_mm')
    assert ok.value == POL.datum_budget_mm
    with pytest.raises(ValidationError):  # loosened policy value
        CheckLimit(name='n', value=1.0, unit='mm', comparison='abs_le', source='policy:datum_budget_mm')
    with pytest.raises(ValidationError):
        CheckLimit(name='n', value=1.0, unit='mm', comparison='le', source='reviewed:beam.length',
                   scale_refs=('measured:volume',))
    with pytest.raises(ValidationError):
        CheckLimit(name='n', value=float('nan'), unit='mm', comparison='le', source='reviewed:beam.length')


def test_plan_rejects_missing_or_unrequired_entries(plan):
    base = plan.candidates[0]
    short = CandidateChecks(candidate_sha256=H, entries=base.entries[:-1])
    with pytest.raises(ValidationError):
        CheckPlan(**{**plan.model_dump(), 'candidates': (short,)})
    loose = tuple(e.model_copy(update={'required': False}) if e.check_id == 'delivered.solid' else e
                  for e in base.entries)
    with pytest.raises(ValidationError):
        CheckPlan(**{**plan.model_dump(), 'candidates': (CandidateChecks(candidate_sha256=H, entries=loose),)})


# ---- evidence record builders ----

def lim(value=1e-3, name='dimension', source='reviewed:beam.dimensional_tolerance', comparison='le'):
    return CheckLimit(name=name, value=value, unit='mm', comparison=comparison, source=source)


def meas(value=0.0, rep='brep', limit=None, satisfied=True):
    return Measurement(name='dimension', value=value, unit='mm', representation=rep,
                       limit=limit or lim(), satisfied=satisfied)


def check_ev(**over):
    data = dict(check_id='delivered.length', category='geometry', required=True, state='passed',
                coverage='measured', method='same_kernel_reread', method_version='v1',
                representation='brep', error_bound=ErrorBound(value=1e-7, unit='mm', source='occt_tol'),
                measurements=(meas(),), reason=None)
    data.update(over)
    return CheckEvidence(**data)


def bindings(state='bound'):
    sig = FaceSignature(normal=(1.0, 0.0, 0.0), center=(1.0, 0.0, 0.0), area=1.0)
    return (FeatureBinding(feature='beam.solid', state='bound', match_count=1),
            FeatureBinding(feature='beam.root_section', state='bound', match_count=1, signature=sig),
            FeatureBinding(feature='beam.free_end', state='bound', match_count=1, signature=sig))


def record(checks=None, scope='checked_design', verdict='verified', binds=None, **over):
    data = dict(revision_id=RID, job_id=JID, candidate_sha256=H, claim_scope=scope, manifest_sha256=H,
                step_sha256=H, glb_sha256=None, check_plan_sha256=H, method_version='v1',
                native_versions=(NativeVersion(name='OCP', version='7.8.1'),),
                bindings=bindings() if binds is None else binds,
                checks=(check_ev(),) if checks is None else checks, verdict=verdict, reasons=())
    data.update(over)
    return DeliveredEvidence(**data)


def test_valid_record_roundtrips_and_hashes():
    rec = record()
    assert evidence_sha256(rec) == evidence_sha256(DeliveredEvidence.model_validate(rec.model_dump()))
    assert len(evidence_sha256(rec)) == 64


def test_identity_only_cannot_be_passed():
    with pytest.raises(ValidationError):
        check_ev(coverage='identity_only')


def test_passed_geometry_needs_measured_coverage_and_measurement():
    for cov in ('policy_report', 'not_evaluated'):
        with pytest.raises(ValidationError):
            check_ev(coverage=cov)
    with pytest.raises(ValidationError):
        check_ev(measurements=())
    with pytest.raises(ValidationError):
        check_ev(error_bound=None)
    with pytest.raises(ValidationError):  # an unsatisfied measurement cannot back a pass
        check_ev(measurements=(meas(value=1.0, satisfied=False),))


def test_measurement_satisfied_must_follow_the_limit():
    with pytest.raises(ValidationError):
        meas(value=1.0, satisfied=True)
    with pytest.raises(ValidationError):
        meas(value=0.0, satisfied=False)
    assert meas(value=1.0, satisfied=False).satisfied is False


def test_limit_must_be_a_plan_limit_when_checked_against_plan(plan):
    cand = plan.candidates[0]
    dim_limits = {e.check_id: e for e in cand.entries}['delivered.length'].limits
    good = meas(limit=dim_limits[0], value=0.0)
    rec = record(checks=(check_ev(measurements=(good,)),), check_plan_sha256=check_plan_sha256(plan),
                 candidate_sha256=cand.candidate_sha256)
    with pytest.raises(ValueError):  # only one of the twelve plan checks present
        check_evidence_against_plan(rec, plan)
    foreign = check_ev(measurements=(meas(limit=lim(value=0.5)),))
    full = tuple(check_ev(check_id=e.check_id, category=e.category, required=e.required,
                          measurements=(good,))
                 for e in cand.entries if e.category == 'geometry' and e.check_id == 'delivered.length')
    assert full and foreign
    rec_bad = record(checks=(foreign,), check_plan_sha256=check_plan_sha256(plan),
                     candidate_sha256=cand.candidate_sha256)
    with pytest.raises(ValueError):
        check_evidence_against_plan(rec_bad, plan)


def test_mesh_measurement_cannot_satisfy_brep_check():
    with pytest.raises(ValidationError):
        check_ev(measurements=(meas(rep='mesh'),))
    with pytest.raises(ValidationError):
        check_ev(representation='none')
    mesh = check_ev(check_id='delivered.preview_extent', category='preview', representation='mesh',
                    measurements=(meas(rep='mesh'),))
    assert mesh.representation == 'mesh'
    with pytest.raises(ValidationError):
        check_ev(check_id='delivered.preview_extent', category='preview', representation='mesh',
                 measurements=(meas(rep='brep'),))


def physics(**over):
    data = dict(check_id='physics.report_inputs', category='physics', required=True, state='passed',
                coverage='policy_report', method='report_cross_bind', method_version='v1',
                representation='brep', measurements=(meas(),))
    data.update(over)
    return CheckEvidence(**data)


def test_physics_coverage_never_measured():
    assert physics().coverage == 'policy_report'
    with pytest.raises(ValidationError):
        physics(coverage='measured')
    with pytest.raises(ValidationError):
        physics(coverage='identity_only')
    with pytest.raises(ValidationError):
        physics(measurements=())


@pytest.mark.parametrize('cat', ['manufacturing', 'standards'])
def test_manufacturing_and_standards_always_not_evaluated(cat):
    ok = CheckEvidence(check_id=f'{cat}.not_evaluated', category=cat, required=False,
                       state='not_evaluated', coverage='not_evaluated', method='none',
                       method_version='v1', representation='none')
    assert ok.state == 'not_evaluated'
    for over in ({'state': 'passed'}, {'state': 'failed', 'reason': 'out_of_scope'},
                 {'coverage': 'measured'}, {'required': True}):
        data = ok.model_dump()
        data.update(over)
        with pytest.raises(ValidationError):
            CheckEvidence(**data)


def test_verdict_is_never_accepted_and_must_match_checks():
    with pytest.raises(ValidationError):
        record(verdict='accepted')
    failed = check_ev(state='failed', coverage='measured', reason='limit_exceeded',
                      measurements=(meas(value=1.0, satisfied=False),))
    with pytest.raises(ValidationError):  # verified despite a failed required check
        record(checks=(failed,), verdict='verified')
    assert record(checks=(failed,), verdict='failed').verdict == 'failed'
    with pytest.raises(ValidationError):
        record(checks=(check_ev(),), verdict='failed')
    unavailable = check_ev(state='unavailable', coverage='not_evaluated', reason='lock_mismatch',
                           measurements=())
    assert record(checks=(unavailable,), verdict='unavailable').verdict == 'unavailable'
    amb = check_ev(state='unavailable', coverage='not_evaluated', reason='binding_ambiguous',
                   measurements=())
    assert record(checks=(amb, failed.model_copy(update={'check_id': 'delivered.width'})),
                  verdict='ambiguous').verdict == 'ambiguous'
    optional_failed = check_ev(required=False, state='failed', reason='limit_exceeded',
                               measurements=(meas(value=1.0, satisfied=False),),
                               check_id='delivered.datum')
    assert derive_verdict((check_ev(), optional_failed)) == 'failed'  # amended: measured failure blocks
    optional_unmeasured = check_ev(required=False, state='failed', coverage='not_evaluated',
                                   reason='limit_exceeded', measurements=(), check_id='delivered.datum')
    assert derive_verdict((check_ev(), optional_unmeasured)) == 'verified'
    assert derive_verdict(()) == 'unavailable'


def test_verified_beam_record_needs_all_features_bound():
    with pytest.raises(ValidationError):
        record(binds=())
    with pytest.raises(ValidationError):
        record(binds=bindings()[:2])


def test_mock_backend_never_passes():
    unavailable = check_ev(state='unavailable', coverage='not_evaluated', reason='mock_backend',
                           measurements=())
    rec = record(checks=(unavailable,), verdict='unavailable', reasons=('mock_backend',), binds=())
    assert rec.verdict == 'unavailable'
    with pytest.raises(ValidationError):
        record(reasons=('mock_backend',))


# ---- D10 label invariants ----

POLICY_LIM = CheckLimit(name='dimension', value=POL.metric_absolute_error, unit='mm', comparison='le',
                        source='policy:metric_absolute_error')


def regen_check(**over):
    data = dict(check_id='regen.volume_check', category='geometry', required=True, state='passed',
                coverage='measured', method='picogk_inspect', method_version='v1', representation='mesh',
                error_bound=ErrorBound(value=0.1, unit='mm', source='voxel'),
                measurements=(meas(rep='mesh', limit=POLICY_LIM),))
    data.update(over)
    return CheckEvidence(**data)


def not_eval(cat):
    return CheckEvidence(check_id=f'{cat}.not_evaluated', category=cat, required=False,
                         state='not_evaluated', coverage='not_evaluated', method='none',
                         method_version='v1', representation='none', reason='out_of_scope')


@pytest.mark.parametrize('scope', ['geometry_checks_only', 'screening_only'])
def test_label_invariants_for_non_beam_scopes(scope):
    good = record(checks=(regen_check(), not_eval('physics'), not_eval('manufacturing')), scope=scope,
                  binds=())
    assert good.claim_scope == scope

    def bad(**kw):
        with pytest.raises(ValidationError):
            record(scope=scope, binds=kw.pop('binds', ()), **kw)

    bad(checks=(regen_check(),), binds=bindings())
    bad(checks=(regen_check(check_id='beam.length_check'),))
    bad(checks=(regen_check(check_id='delivered.length'),))
    bad(checks=(physics(),))
    bad(checks=(regen_check(method='checked_design'),))
    bad(checks=(regen_check(method='accepted for manufacture'),))
    bad(checks=(regen_check(), regen_check(check_id='regen.other', method='beam.solid')))
    bad(checks=(regen_check(),), reasons=('mock_backend',), verdict='unavailable')
    for cat in ('physics', 'manufacturing', 'standards'):
        data = not_eval(cat).model_dump()
        data.update(state='failed', reason='out_of_scope')
        if cat == 'physics':
            bad(checks=(regen_check(), CheckEvidence(**data)), verdict='failed')


# ---- delivered inputs ----

def face(**over):
    data = dict(surface='plane', normal=(-1.0, 0.0, 0.0), center=(0.0, 5.0, 5.0), area=100.0,
                bounds=(0.0, 0.0, 0.0, 0.0, 10.0, 10.0))
    data.update(over)
    return FaceRecord(**data)


def table(n):
    return FaceTable(solid_count=1, closed_solid_count=1, bounds=(0.0, 0.0, 0.0, 100.0, 10.0, 10.0),
                     volume=1.0, area=1.0, max_topology_tolerance=1e-7,
                     native_versions=(NativeVersion(name='OCP', version='7.8.1'),),
                     faces=tuple(face() for _ in range(n)))


def test_face_table_bound_at_64_faces():
    assert len(table(64).faces) == 64
    with pytest.raises(ValidationError):
        table(65)


def test_face_record_rules():
    with pytest.raises(ValidationError):
        face(normal=(2.0, 0.0, 0.0))
    with pytest.raises(ValidationError):
        face(normal=None)
    with pytest.raises(ValidationError):
        face(surface='cylinder')
    assert face(surface='cylinder', normal=None).normal is None
    with pytest.raises(ValidationError):
        face(center=(float('inf'), 0.0, 0.0))
    with pytest.raises(ValidationError):
        face(bounds=(1.0, 0.0, 0.0, 0.0, 1.0, 1.0))


def test_non_ascii_names_rejected():
    bad = 'face' + chr(233)
    with pytest.raises(ValidationError):
        face(name=bad)
    assert face(name='FACE_1').name == 'FACE_1'
    mesh = dict(triangle_count=12, vertex_count=8, bounds_mm=(0.0, 0.0, 0.0, 1.0, 1.0, 1.0),
                mesh_bound_mm=0.01)
    assert MeshSummary(**mesh, node_names=('node',)).node_names == ('node',)
    with pytest.raises(ValidationError):
        MeshSummary(**mesh, node_names=(bad,))
    with pytest.raises(ValidationError):
        MeshSummary(**mesh, node_names=tuple('n' for _ in range(65)))


def test_feature_binding_state_rules():
    sig = FaceSignature(normal=(1.0, 0.0, 0.0), center=(1.0, 0.0, 0.0), area=1.0)
    assert FeatureBinding(feature='beam.free_end', state='missing', match_count=0).signature is None
    assert FeatureBinding(feature='beam.free_end', state='ambiguous', match_count=2).state == 'ambiguous'
    with pytest.raises(ValidationError):
        FeatureBinding(feature='beam.free_end', state='bound', match_count=2, signature=sig)
    with pytest.raises(ValidationError):
        FeatureBinding(feature='beam.free_end', state='bound', match_count=1)
    with pytest.raises(ValidationError):
        FeatureBinding(feature='beam.free_end', state='missing', match_count=0, signature=sig)
    with pytest.raises(ValidationError):
        FeatureBinding(feature='beam.free_end', state='ambiguous', match_count=1)


# ---- purity ----

def test_import_loads_no_native_provider_or_web_module():
    code = (
        'import sys, json\n'
        'import engineering.program.evidence\n'
        'print(json.dumps(sorted(m for m in ("cadquery", "OCP", "trimesh", "google.genai", "fastapi",'
        ' "httpx") if m in sys.modules)))\n')
    env = dict(os.environ, PYTHONPATH=f'{ROOT / "backend"}{os.pathsep}{ROOT}')
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env,
                         timeout=120, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []

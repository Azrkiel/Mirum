"""F07 task 1b: pure feature binder, check evaluator, verdict amendments and the face-table corpus.

Everything here is pure (no native code). Dimensions and tolerances come from the reviewed check plan;
the face table supplies geometry only.

Face-table corpus format (backend/tests/fixtures/program/f07/face_tables/*.json, <= 256 KiB each):
    {"id": str,                      # unique, equals the file stem
     "description": str,
     "candidate_height_mm": float,   # reviewed candidate (beam.mir heights 5, 10, 15; length 200, width 10)
     "table": <FaceTable JSON>,      # solid_count, closed_solid_count, bounds, volume, area,
                                     # max_topology_tolerance, native_versions, faces[<=64]
     "mesh": <MeshSummary JSON> | null,
     "expected": {"verdict": "verified|failed|ambiguous|unavailable",
                  "bindings": {"beam.solid": state, "beam.root_section": state, "beam.free_end": state}}}
state is bound | missing | ambiguous. The native versions in a case must be cadquery 2.6.1 and OCP 7.8.1
(the lock used here) unless the case is about a lock mismatch. Report inputs are synthesised from the
REVIEWED candidate (length 200, height = candidate_height_mm, Iy = 10 * h^3 / 12), never from the table.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from engineering.language.semantic import compile_source
from engineering.program.evidence import (
    CheckEntry, CheckEvidence, CheckLimit, CheckPlan, CandidateChecks, FaceTable, MeshSummary,
    ReportInputs, bind_features, build_check_plan, build_delivered_evidence, check_evidence_against_plan,
    derive_verdict, evaluate,
)
from engineering.program.ir import execution_lock_v0_2
from engineering.program.kernel import NativeVersion, ProbeResult, backend_lock, make_description
from engineering.program.planning import _candidates_from_content

P03 = Path(__file__).resolve().parent / 'fixtures' / 'program' / 'p03'
CORPUS = Path(__file__).resolve().parent / 'fixtures' / 'program' / 'f07' / 'face_tables'
MAX_CASE_BYTES = 256 * 1024
H64 = 'b' * 64
RID = '1' * 32
JID = '2' * 32
LOCK_VERSIONS = (NativeVersion(name='cadquery', version='2.6.1'), NativeVersion(name='OCP', version='7.8.1'))
NV = [{'name': 'cadquery', 'version': '2.6.1'}, {'name': 'OCP', 'version': '7.8.1'}]


@pytest.fixture(scope='module')
def compiled():
    return compile_source((P03 / 'beam.mir').read_bytes())


@pytest.fixture(scope='module')
def plan(compiled):
    desc = make_description('occt', mode='native', adapter_version='adapter_1', tolerances=())
    probe = ProbeResult(backend='occt', mode='native', available=True, native_versions=LOCK_VERSIONS)
    lock = execution_lock_v0_2(compiled.ir, backend_lock(desc, probe))
    return build_check_plan(compiled.ir, lock, _candidates_from_content(compiled.brief_content),
                            mesh_convention_pinned=True)


def _sha(plan_, height):
    # candidates carry their height only in the digest; recover it from the plan limits
    for cand in plan_.candidates:
        for e in cand.entries:
            if e.check_id == 'delivered.height':
                if {lim.name: lim for lim in e.limits}['reviewed'].value == height:
                    return cand.candidate_sha256
    raise AssertionError(f'no candidate with height {height}')


def face(normal, center, area, surface='plane', name=None):
    return {'surface': surface, 'normal': normal, 'center': center, 'area': area,
            'bounds': [center[0], center[1], center[2], center[0], center[1], center[2]], 'name': name}


def box_table(length=200.0, width=10.0, height=15.0, tol=1e-7, **over):
    w, h = width, height
    data = {
        'solid_count': 1, 'closed_solid_count': 1, 'bounds': [0, 0, 0, length, w, h],
        'volume': length * w * h, 'area': 2 * (w * h + length * w + length * h),
        'max_topology_tolerance': tol, 'native_versions': copy.deepcopy(NV),
        'faces': [
            face([-1, 0, 0], [0, w / 2, h / 2], w * h),
            face([1, 0, 0], [length, w / 2, h / 2], w * h),
            face([0, -1, 0], [length / 2, 0, h / 2], length * h),
            face([0, 1, 0], [length / 2, w, h / 2], length * h),
            face([0, 0, -1], [length / 2, w / 2, 0], length * w),
            face([0, 0, 1], [length / 2, w / 2, h], length * w),
        ]}
    data.update(over)
    return data


def table_of(data) -> FaceTable:
    return FaceTable.model_validate_json(json.dumps(data))


def mesh_of(bounds=(0, 0, 0, 200, 10, 15), triangles=12, bound=0.1):
    return MeshSummary(triangle_count=triangles, vertex_count=8 if triangles else 0,
                       bounds_mm=bounds, mesh_bound_mm=bound)


def reviewed_report(h=15.0, **over):
    data = dict(physics_passed=True, section_iy_mm4=10.0 * h ** 3 / 12.0, length_mm=200.0, height_mm=h)
    data.update(over)
    return ReportInputs(**data)


def run(plan_, data, *, mesh=None, h=15.0, report='auto', versions=LOCK_VERSIONS):
    table = table_of(data)
    report = reviewed_report(h) if report == 'auto' else report
    sha = _sha(plan_, h)
    checks = evaluate(plan_, sha, table, mesh, report, versions, method_version='v1')
    by_id = {c.check_id: c for c in checks}
    entries = next(c for c in plan_.candidates if c.candidate_sha256 == sha).entries
    binds = {b.feature: b for b in bind_features(table, entries)}
    return by_id, binds, derive_verdict(checks), sha


def entries_for(plan_, h=15.0):
    sha = _sha(plan_, h)
    return next(c for c in plan_.candidates if c.candidate_sha256 == sha).entries


def states(binds):
    return {f: b.state for f, b in binds.items()}


ALL_BOUND = {'beam.solid': 'bound', 'beam.root_section': 'bound', 'beam.free_end': 'bound'}


# ---- the ideal box ----

def test_ideal_box_binds_and_verifies(plan):
    checks, binds, verdict, _ = run(plan, box_table())
    assert states(binds) == ALL_BOUND
    assert verdict == 'verified'
    for cid in ('delivered.solid', 'delivered.root_section', 'delivered.free_end', 'delivered.length',
                'delivered.width', 'delivered.height', 'delivered.datum', 'delivered.volume'):
        assert checks[cid].state == 'passed' and checks[cid].coverage == 'measured'
        assert checks[cid].representation == 'brep'
        assert checks[cid].error_bound.source == 'occt_topology_tolerance'
    assert checks['physics.report_inputs'].coverage == 'policy_report'
    for cid in ('manufacturing.not_evaluated', 'standards.not_evaluated'):
        assert checks[cid].state == 'not_evaluated'
    sig = binds['beam.root_section'].signature
    assert sig.center == (0.0, 5.0, 7.5) and sig.area == 150.0 and sig.normal == (-1.0, 0.0, 0.0)


def test_assembled_record_is_validated_and_matches_the_plan(plan):
    sha = _sha(plan, 15.0)
    rec = build_delivered_evidence(plan, sha, table_of(box_table()), mesh_of(), reviewed_report(), LOCK_VERSIONS,
                                   revision_id=RID, job_id=JID, manifest_sha256=H64, step_sha256=H64,
                                   glb_sha256=H64, method_version='v1')
    assert rec.verdict == 'verified' and rec.reasons == ()
    check_evidence_against_plan(rec, plan)
    bad = build_delivered_evidence(plan, sha, table_of(box_table(length=199.0)), None, reviewed_report(),
                                   LOCK_VERSIONS, revision_id=RID, job_id=JID, manifest_sha256=H64,
                                   step_sha256=H64, glb_sha256=None, method_version='v1')
    assert bad.verdict == 'failed' and 'binding_missing' in bad.reasons
    check_evidence_against_plan(bad, plan)


def test_plan_limits_carry_the_reviewed_values(plan):
    by = {e.check_id: {lim.name: lim for lim in e.limits} for e in entries_for(plan)}
    assert by['delivered.root_section']['width'].value == 10.0
    assert by['delivered.root_section']['height'].value == 15.0
    assert by['delivered.free_end']['length'].value == 200.0
    assert by['delivered.volume']['reviewed_volume'].value == 30000.0


# ---- limit pins (use only the plan limits; math.nextafter for the exact boundary) ----

def with_face(data, index, **fields):
    data = copy.deepcopy(data)
    data['faces'][index].update(fields)
    return data


def test_datum_limit_pin_on_root_center(plan):
    limit = 1e-6  # policy datum_budget_mm
    at = with_face(box_table(), 0, center=[limit, 5, 7.5])
    over = with_face(box_table(), 0, center=[math.nextafter(limit, 1.0), 5, 7.5])
    assert run(plan, at)[1]['beam.root_section'].state == 'bound'
    assert run(plan, over)[1]['beam.root_section'].state == 'missing'


def test_normal_error_limit_pin_on_root_normal(plan):
    limit = 1e-9  # policy face_normal_error
    at = with_face(box_table(), 0, normal=[-1, 0, limit])
    over = with_face(box_table(), 0, normal=[-1, 0, math.nextafter(limit, 1.0)])
    assert run(plan, at)[1]['beam.root_section'].state == 'bound'
    assert run(plan, over)[1]['beam.root_section'].state == 'missing'


def test_topology_tolerance_limit_pin(plan):
    limit = 1e-6  # policy max_topology_tolerance_mm
    ok = run(plan, box_table(tol=limit))[0]['delivered.solid']
    assert ok.state == 'passed'
    bad = run(plan, box_table(tol=math.nextafter(limit, 1.0)))[0]['delivered.solid']
    assert bad.state == 'failed' and bad.reason == 'limit_exceeded'


def test_area_and_position_steps(plan):
    # area tolerance for 10x15 is max(1e-6, 1e-8 * 150) = 1.5e-6: documented steps either side
    assert run(plan, with_face(box_table(), 0, area=150.0 + 1e-6))[1]['beam.root_section'].state == 'bound'
    assert run(plan, with_face(box_table(), 0, area=150.0 + 1e-5))[1]['beam.root_section'].state == 'missing'
    # free end position tolerance is the reviewed 0.01 mm (plus 2 * topology tolerance)
    assert run(plan, with_face(box_table(), 1, center=[200.009, 5, 7.5]))[1]['beam.free_end'].state == 'bound'
    assert run(plan, with_face(box_table(), 1, center=[200.011, 5, 7.5]))[1]['beam.free_end'].state == 'missing'


# ---- ambiguity, absence, order and names ----

def test_two_root_matches_is_ambiguous_verdict(plan):
    data = box_table()
    data['faces'].append(copy.deepcopy(data['faces'][0]))
    checks, binds, verdict, _ = run(plan, data)
    assert binds['beam.root_section'].state == 'ambiguous' and binds['beam.root_section'].match_count == 2
    assert binds['beam.root_section'].signature is None
    assert checks['delivered.root_section'].reason == 'binding_ambiguous'
    assert verdict == 'ambiguous'


def test_no_root_match_is_missing_and_failed(plan):
    data = box_table()
    del data['faces'][0]
    checks, binds, verdict, _ = run(plan, data)
    assert binds['beam.root_section'].state == 'missing' and binds['beam.root_section'].match_count == 0
    assert checks['delivered.root_section'].state == 'failed'
    assert checks['delivered.root_section'].reason == 'binding_missing'
    assert verdict == 'failed'


def test_permuted_face_order_binds_the_same_signatures(plan):
    base = box_table()
    ref = run(plan, base)[1]
    for order in ([5, 4, 3, 2, 1, 0], [2, 0, 4, 1, 5, 3]):
        data = copy.deepcopy(base)
        data['faces'] = [base['faces'][i] for i in order]
        checks, binds, verdict, _ = run(plan, data)
        assert binds == ref and verdict == 'verified'


def test_names_never_bind(plan):
    base = box_table()
    decoy = face([0, 0, 1], [100, 5, 15], 2000.0, name='root_section')  # wrong geometry, right name
    data = copy.deepcopy(base)
    data['faces'].append(decoy)
    ref = run(plan, base)[1]
    checks, binds, verdict, _ = run(plan, data)
    assert binds == ref and verdict == 'verified'
    # a correct face renamed to something else still binds; every face renamed keeps the evidence equal
    relabel = copy.deepcopy(base)
    for i, f in enumerate(relabel['faces']):
        f['name'] = f'free_end_{i}'
    checks2, binds2, verdict2, _ = run(plan, relabel)
    assert binds2 == ref and verdict2 == 'verified'
    assert {k: (v.state, v.measurements) for k, v in checks2.items()} == {
        k: (v.state, v.measurements) for k, v in run(plan, base)[0].items()}


# ---- wrong shapes ----

def test_swapped_width_and_height_fail_checks(plan):
    checks, binds, verdict, _ = run(plan, box_table(width=15.0, height=10.0))
    assert states(binds) == ALL_BOUND  # end faces keep the same area
    assert checks['delivered.width'].state == 'failed' and checks['delivered.height'].state == 'failed'
    assert verdict == 'failed'


def test_off_datum_fails(plan):
    data = box_table()
    data['bounds'] = [5.0, 0.0, 0.0, 205.0, 10.0, 15.0]
    checks, _, verdict, _ = run(plan, data)
    assert checks['delivered.datum'].state == 'failed' and verdict == 'failed'


def test_length_just_past_tolerance_fails(plan):
    just_in = run(plan, box_table(length=200.009))[0]['delivered.length']
    assert just_in.state == 'passed'
    out = run(plan, box_table(length=200.011))
    assert out[0]['delivered.length'].state == 'failed' and out[2] == 'failed'


def test_extra_non_plane_face_is_ignored_by_the_binder(plan):
    data = box_table()
    data['faces'].append({'surface': 'cylinder', 'normal': None, 'center': [100, 5, 7.5], 'area': 50.0,
                          'bounds': [90, 0, 0, 110, 10, 15], 'name': None})
    checks, binds, verdict, _ = run(plan, data)
    assert states(binds) == ALL_BOUND and verdict == 'verified'  # nothing else changed
    holed = copy.deepcopy(data)
    holed['volume'] = 30000.0 - 1000.0  # a real hole removes volume: the volume check catches it
    checks, binds, verdict, _ = run(plan, holed)
    assert states(binds) == ALL_BOUND
    assert checks['delivered.volume'].state == 'failed' and verdict == 'failed'


def test_two_solids_are_ambiguous(plan):
    checks, binds, verdict, _ = run(plan, box_table(solid_count=2, closed_solid_count=2))
    assert binds['beam.solid'].state == 'ambiguous' and binds['beam.solid'].match_count == 2
    assert checks['delivered.length'].reason == 'binding_ambiguous'
    assert verdict == 'ambiguous'


def test_open_or_absent_solid_is_missing(plan):
    for data in (box_table(solid_count=1, closed_solid_count=0), box_table(solid_count=0, closed_solid_count=0)):
        checks, binds, verdict, _ = run(plan, data)
        assert binds['beam.solid'].state == 'missing' and verdict == 'failed'
        assert checks['delivered.solid'].reason == 'binding_missing'


def test_split_root_face_fails_closed(plan):
    data = box_table()
    half = face([-1, 0, 0], [0, 5, 3.75], 75.0)
    other = face([-1, 0, 0], [0, 5, 11.25], 75.0)
    data['faces'][0:1] = [half, other]
    checks, binds, verdict, _ = run(plan, data)
    assert binds['beam.root_section'].state == 'missing' and verdict == 'failed'


# ---- coverage, representation, lock, report ----

def test_no_check_is_identity_only_and_mesh_never_satisfies_brep(plan):
    checks, _, _, _ = run(plan, box_table(), mesh=mesh_of())
    for c in checks.values():
        assert c.coverage != 'identity_only'
        if c.category in ('geometry', 'physics'):
            assert all(m.representation == 'brep' for m in c.measurements)
    with pytest.raises(ValidationError):
        CheckEvidence(check_id='delivered.length', category='geometry', required=True, state='passed',
                      coverage='identity_only', method='m', method_version='v1', representation='brep')
    donor = checks['delivered.length']
    mesh_m = donor.measurements[0].model_copy(update={'representation': 'mesh'})
    with pytest.raises(ValidationError):
        CheckEvidence(**{**donor.model_dump(), 'measurements': (mesh_m,)})


def test_lock_native_version_mismatch_is_unavailable(plan):
    other = (NativeVersion(name='cadquery', version='2.6.1'), NativeVersion(name='OCP', version='7.9.0'))
    checks, _, verdict, _ = run(plan, box_table(), mesh=mesh_of(), versions=other)
    for cid in ('delivered.solid', 'delivered.root_section', 'delivered.free_end', 'delivered.length',
                'delivered.datum', 'delivered.volume', 'delivered.preview_extent', 'physics.report_inputs'):
        assert checks[cid].state == 'unavailable' and checks[cid].reason == 'lock_mismatch'
    assert verdict == 'unavailable'


def test_report_inputs_cross_binding(plan):
    ok = run(plan, box_table())[0]['physics.report_inputs']
    assert ok.state == 'passed' and ok.coverage == 'policy_report'
    for bad in (reviewed_report(section_iy_mm4=10.0 * 15 ** 3 / 12.0 + 1.0), reviewed_report(length_mm=199.0),
                reviewed_report(height_mm=14.0), reviewed_report(physics_passed=False)):
        checks, _, verdict, _ = run(plan, box_table(), report=bad)
        assert checks['physics.report_inputs'].state == 'failed'
        assert checks['physics.report_inputs'].reason == 'report_mismatch'
        assert verdict == 'failed'
    checks, _, verdict, _ = run(plan, box_table(), report=None)
    assert checks['physics.report_inputs'].state == 'unavailable' and verdict == 'unavailable'


# ---- preview ----

def test_exact_box_mesh_is_not_evaluated_but_verdict_verified(plan):
    checks, _, verdict, _ = run(plan, box_table(), mesh=mesh_of())
    prev = checks['delivered.preview_extent']
    assert prev.state == 'not_evaluated' and prev.reason == 'bound_exceeds_tolerance'
    assert prev.coverage == 'not_evaluated' and prev.required is False
    assert verdict == 'verified'


def test_missing_mesh_is_not_evaluated_when_optional(plan):
    prev = run(plan, box_table(), mesh=None)[0]['delivered.preview_extent']
    assert prev.state == 'not_evaluated' and prev.reason == 'measurement_unavailable'


@pytest.mark.parametrize('bounds,triangles', [
    ((0, 0, 0, 200, 10, 10), 12),          # substituted 10 mm high mesh
    ((0, 0, 0, 200000, 10000, 15000), 12),  # 1000x scaled mesh
    ((0, 0, 0, 200, 10, 15), 0),           # empty mesh
])
def test_bad_meshes_fail_the_verdict(plan, bounds, triangles):
    checks, _, verdict, _ = run(plan, box_table(), mesh=mesh_of(bounds, triangles))
    prev = checks['delivered.preview_extent']
    assert prev.state == 'failed' and prev.coverage == 'measured' and prev.required is False
    assert verdict == 'failed'


def _loose_plan(plan_, tolerance):
    """A plan whose preview tolerance is wide enough for the conservative rule (preview required)."""
    out = []
    for cand in plan_.candidates:
        entries = []
        for e in cand.entries:
            if e.check_id == 'delivered.preview_extent':
                limits = tuple(
                    CheckLimit(name=lim.name, value=tolerance, unit=lim.unit, comparison=lim.comparison,
                               source=lim.source, scale_refs=lim.scale_refs) if lim.name == 'extent' else lim
                    for lim in e.limits)
                e = CheckEntry(**{**e.model_dump(), 'limits': limits, 'required': True})
            entries.append(e)
        out.append(CandidateChecks(candidate_sha256=cand.candidate_sha256, entries=tuple(entries)))
    return CheckPlan(**{**plan_.model_dump(), 'candidates': tuple(out)})


def test_preview_conservative_pass_when_tolerance_allows(plan):
    loose = _loose_plan(plan, 1.0)
    checks, _, verdict, _ = run(loose, box_table(), mesh=mesh_of())
    prev = checks['delivered.preview_extent']
    assert prev.state == 'passed' and prev.required is True and prev.error_bound.value == 0.1
    assert verdict == 'verified'
    assert run(loose, box_table(), mesh=None)[2] == 'unavailable'  # required and absent
    with pytest.raises(ValidationError):  # required although the reviewed tolerance 0.01 can never pass
        _loose_plan(plan, 0.01)


def test_preview_required_flag_follows_the_conservative_rule(plan):
    assert all(not e.required for c in plan.candidates for e in c.entries
               if e.check_id == 'delivered.preview_extent')


def test_mesh_convention_not_pinned_is_not_evaluated(compiled, plan):
    data = plan.model_dump()
    data['mesh_convention_pinned'] = False
    unpinned = CheckPlan.model_validate(data)
    prev = run(unpinned, box_table(), mesh=mesh_of((0, 0, 0, 200, 10, 10)))[0]['delivered.preview_extent']
    assert prev.state == 'not_evaluated' and prev.reason == 'not_pinned'


# ---- corpus ----

def _cases():
    files = sorted(CORPUS.glob('*.json'))
    assert len(files) >= 6, 'the seed corpus has at least six cases'
    return files


@pytest.mark.parametrize('path', _cases(), ids=lambda p: p.stem)
def test_face_table_corpus(plan, path):
    assert path.stat().st_size <= MAX_CASE_BYTES
    with path.open('rb') as handle:
        raw = handle.read(MAX_CASE_BYTES + 1)
    assert len(raw) <= MAX_CASE_BYTES
    case = json.loads(raw.decode('utf-8'))
    assert set(case) == {'id', 'description', 'candidate_height_mm', 'table', 'mesh', 'expected'}
    assert case['id'] == path.stem
    table = FaceTable.model_validate_json(json.dumps(case['table']))
    mesh = None if case['mesh'] is None else MeshSummary.model_validate_json(json.dumps(case['mesh']))
    h = float(case['candidate_height_mm'])
    sha = _sha(plan, h)
    rec = build_delivered_evidence(plan, sha, table, mesh, reviewed_report(h), LOCK_VERSIONS, revision_id=RID,
                                   job_id=JID, manifest_sha256=H64, step_sha256=H64, glb_sha256=None,
                                   method_version='v1')
    check_evidence_against_plan(rec, plan)
    assert rec.verdict == case['expected']['verdict']
    assert {b.feature: b.state for b in rec.bindings} == case['expected']['bindings']
    for c in rec.checks:
        assert c.coverage != 'identity_only'

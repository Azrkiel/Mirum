"""F07 native: OcctAdapter.measure on real CadQuery STEP + GLB bytes (native shard only).

Pins the GLB frame convention on a known box, measures a real beam-sized box through the face-table
worker, binds and evaluates it against the beam.mir check plan, and checks the 64-face cap fails closed.
Skipped when cadquery is absent or off posix (ProcessRunner needs posix process groups).
"""
import asyncio
import hashlib
import os
from pathlib import Path

import pytest

cadquery = pytest.importorskip('cadquery')

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='ProcessRunner needs posix process groups')

ROOT = Path(__file__).resolve().parents[2]
MIR = ROOT / 'backend' / 'tests' / 'fixtures' / 'program' / 'p03' / 'beam.mir'


def run(coro):
    return asyncio.run(coro)


def artifacts(shape, directory, *, name='model'):
    """STEP via the cadquery exporter, GLB exactly as verification/acceptance_worker.py saves it."""
    import cadquery as cq
    from engineering.program.kernel import Artifact, ArtifactRef
    step_path, glb_path = Path(directory) / f'{name}.step', Path(directory) / f'{name}.glb'
    cq.exporters.export(shape, str(step_path))
    assembly = cq.Assembly(shape.val(), name='model')
    assembly.save(str(glb_path), exportType='GLB')

    def make(path, fmt, representation, node):
        data = path.read_bytes()
        ref = ArtifactRef(name=path.name, format=fmt, representation=representation,
                          sha256=hashlib.sha256(data).hexdigest(), size=len(data), node_id=node)
        return Artifact(ref, data)

    return make(step_path, 'step', 'brep', 'beam.solid'), make(glb_path, 'glb', 'mesh', 'beam.preview')


@pytest.fixture(scope='module')
def world():
    from engineering.language.semantic import compile_source
    from engineering.program.adapters.occt import OcctAdapter
    from engineering.program.evidence import build_check_plan
    from engineering.program.kernel import ExecutionLockV0_2
    from engineering.program.planning import ProgramPlan, _candidates_from_content, lock_program, plan_program

    compiled = compile_source(MIR.read_bytes(), source_id='examples/beam.mir')
    adapter = OcctAdapter()
    plan = plan_program(compiled, mode='native', adapter_factory=lambda backend, mode: adapter)
    assert isinstance(plan, ProgramPlan), plan
    lock = run(lock_program(plan, adapter=adapter))
    assert isinstance(lock, ExecutionLockV0_2), lock
    check_plan = build_check_plan(compiled.ir, lock, _candidates_from_content(compiled.brief_content),
                                  mesh_convention_pinned=True)
    return dict(adapter=adapter, plan=plan, lock=lock, check_plan=check_plan)


def test_glb_frame_convention_is_pinned_on_a_known_box(world, tmp_path):
    import cadquery as cq
    from engineering.program.evidence import GLB_F32_REL
    box = cq.Workplane('XY').box(1, 2, 3, centered=False)
    step, glb = artifacts(box, tmp_path)
    out = run(world['adapter'].measure(step, glb, world['plan'].plans[0]))
    assert out.reason is None and out.table is not None, out
    assert out.mesh_error is None and out.mesh is not None
    assert out.mesh.triangle_count >= 12
    assert out.mesh.bounds_mm == pytest.approx((0, 0, 0, 1, 2, 3), abs=GLB_F32_REL * 3)


def test_beam_box_measured_bound_and_evaluated(world, tmp_path):
    import cadquery as cq
    from engineering.program.adapters.occt import OcctAdapter
    from engineering.program.evidence import ReportInputs, bind_features, derive_verdict, evaluate
    box = cq.Workplane('XY').box(200, 10, 15, centered=False)
    step, glb = artifacts(box, tmp_path)
    adapter, plan, check_plan = world['adapter'], world['plan'], world['check_plan']
    out = run(adapter.measure(step, glb, plan.plans[2]))
    assert out.reason is None and out.table is not None, out
    table = out.table
    assert (table.solid_count, table.closed_solid_count, len(table.faces)) == (1, 1, 6)
    assert all(f.surface == 'plane' and f.normal is not None for f in table.faces)
    probe = run(OcctAdapter().probe())
    assert probe.available and table.native_versions == probe.native_versions

    spec = next(p.candidate for p in plan.plans if p.candidate.specification.height_mm == 15.0)
    sha = spec.candidate_sha256
    entries = next(c for c in check_plan.candidates if c.candidate_sha256 == sha).entries
    bindings = {b.feature: b.state for b in bind_features(table, entries)}
    assert bindings == {'beam.solid': 'bound', 'beam.root_section': 'bound', 'beam.free_end': 'bound'}
    report = ReportInputs(physics_passed=True, section_iy_mm4=10.0 * 15.0 ** 3 / 12.0,
                          length_mm=200.0, height_mm=15.0)
    checks = evaluate(check_plan, sha, table, out.mesh, report, world['lock'].backend_lock.native_versions,
                      method_version='f07-native-test')
    assert derive_verdict(checks) == 'verified', [(c.check_id, c.state, c.reason) for c in checks]
    preview = next(c for c in checks if c.check_id == 'delivered.preview_extent')
    assert preview.state == 'not_evaluated' and preview.reason == 'bound_exceeds_tolerance'


def test_over_64_faces_fails_closed_without_a_table(world, tmp_path):
    import cadquery as cq
    prism = cq.Workplane('XY').polygon(64, 20).extrude(5)  # 64 side faces + 2 caps = 66
    assert len(prism.faces().vals()) == 66
    step, _ = artifacts(prism, tmp_path)
    out = run(world['adapter'].measure(step, None, world['plan'].plans[0]))
    assert out.table is None and out.mesh is None and out.reason == 'too_many_faces'


# =========================================================================== F07 task 5
# P07 re-shown with the delivered-verification gate ON, and the native tamper corpus (P10).
#
# One module-scoped world runs the real beam job once (5/10/15 mm, 15 mm accepted), publishes it through a
# real BundlePublisher into a real SQLiteStore, and the REAL RevisionService default verifier measures the
# delivered bytes with the real OcctAdapter. Each tamper case then re-signs one or more published files of
# that bundle with resign_bundle (files, manifest, archive, publications row and jobs row stay consistent, so
# publisher.validate still passes: only the F07 verifier can catch it), verifies the tampered revision with
# a fresh revision id (a verified row is never overwritten) and restores the original bytes afterwards.

import json  # noqa: E402
import re  # noqa: E402
import struct  # noqa: E402

from engineering.bundles import encode  # noqa: E402
from engineering.program.delivered import (  # noqa: E402
    DELIVERED_BINDING_AMBIGUOUS, DELIVERED_CHECK_FAILED, DeliveredVerifier,
)
from engineering.program.evidence import evidence_sha256  # noqa: E402
from tests.test_program_delivered import evolve, resign_bundle  # noqa: E402
from tests.test_program_revisions import (  # noqa: E402
    BEAM, CLIENT, REJECTED, land_staged, stage_final, step_sha,
)
from tests.test_program_revisions_native import (  # noqa: E402
    WAIT_S, close_world, counting_adapter, head_id, job_run, make_occt,
)

PAYLOADS = ('model.step', 'preview.glb', 'report.json', 'selected_spec.json', 'attempts.json')
GLB_JSON, GLB_BIN = 0x4E4F534A, 0x004E4942


async def open_gated_world(root, adapter, occt):
    """Like test_program_revisions_native.open_world, but with the REAL delivered gate (default verifier)."""
    from engineering.briefs import BriefService
    from engineering.bundles import BundlePublisher
    from engineering.jobs import JobController
    from engineering.program.transactions import RevisionService
    from engineering.store import SQLiteBriefRepository, SQLiteStore

    class W:
        pass

    w = W()
    w.store = SQLiteStore(root)
    w.jobs = w.svc = None
    try:
        w.pub = BundlePublisher(w.store)
        w.briefs = BriefService(SQLiteBriefRepository(w.store))
        w.jobs = JobController(w.store, publisher=w.pub, adapter=adapter)
        w.svc = RevisionService(w.store, w.jobs, w.pub, w.briefs,
                                adapter_factory=lambda backend, mode='native': occt)
    except BaseException:
        if w.jobs is not None:
            await w.jobs.close()
        w.store.close()
        raise
    w.pid = None
    return w


class Gated:
    """Module-scoped gated world, its own event loop and the verified 15 mm revision."""

    def __init__(self, root):
        from engineering.language.semantic import compile_source
        self.root = Path(root)
        self.loop = asyncio.new_event_loop()
        self.occt = make_occt()
        self.calls = []
        self.seq = 0
        try:
            self.w = self.run(open_gated_world(self.root / 'store', counting_adapter(), self.occt))
            self.w.pid = self.w.svc.create_program(CLIENT)
            verifier = self.w.svc.verifier
            assert isinstance(verifier, DeliveredVerifier)
            original = verifier.verify

            async def counting(revision, ir):
                self.calls.append(revision.revision_id)
                return await original(revision, ir)

            verifier.verify = counting
            from engineering.program.planning import _candidates_from_content
            compiled = compile_source(BEAM.encode('utf-8'), source_id='beam.mir')
            self.ir = compiled.ir
            self.candidates = _candidates_from_content(compiled.brief_content)
            self.first = self.run(self.land_first())
            self.originals = {name: (self.w.pub.directory(self.first.job_id) / name).read_bytes()
                              for name in PAYLOADS}
            self.verifier = DeliveredVerifier(self.w.store, self.w.pub,
                                              lambda backend, mode='native': self.occt, timeout_s=120.0)
        except BaseException:
            self.loop.close()
            raise

    def run(self, coro, timeout=WAIT_S):
        return self.loop.run_until_complete(asyncio.wait_for(coro, timeout))

    async def land_first(self):
        rev, brief_id = await stage_final(self.w, BEAM, None)
        return await land_staged(self.w, rev, brief_id)

    def close(self):
        try:
            self.run(close_world(self.w), 120)
        finally:
            self.loop.close()

    def mkdir(self, name):
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def stored(self):
        return self.w.svc.evidence(self.first.revision_id, client=CLIENT)

    def spec_of(self, height):
        return next(c for c in self.candidates if c.specification.height_mm == height)

    def tamper(self, replacements):
        """Re-sign replacements into the real bundle, verify it as a fresh revision, restore the originals.

        Returns (record, match_problem). publisher.validate must still accept the re-signed bundle."""
        w, job = self.w, self.first.job_id
        bundle = resign_bundle(w.store, w.pub, job, replacements)
        try:
            w.pub.validate(job, bundle)  # the tampered bundle is internally consistent
            self.seq += 1
            rev = evolve(self.first, revision_id=f'{0xf07a0000 + self.seq:032x}', bundle=bundle)
            record = self.run(self.verifier.verify(rev, self.ir))
            with w.store.transaction() as db:
                problem = self.verifier.evidence.match_problem(db, rev.revision_id, job_id=job, bundle=bundle)
        finally:
            resign_bundle(w.store, w.pub, job, dict(self.originals))
        return record, problem


@pytest.fixture(scope='module')
def gated(tmp_path_factory):
    g = Gated(tmp_path_factory.mktemp('f07native'))
    try:
        yield g
    finally:
        g.close()


def bind_states(record):
    return {b.feature: b.state for b in record.bindings}


def check_of(record, check_id):
    return next(c for c in record.checks if c.check_id == check_id)


def step_of(g, shape, name):
    step, _ = artifacts(shape, g.mkdir(name), name=name)
    return step.data


def glb_of(g, shape, name):
    _, glb = artifacts(shape, g.mkdir(name), name=name)
    return glb.data


def glb_split(data):
    """(gltf dict, BIN chunk bytes or None) of a GLB written by CadQuery."""
    total = struct.unpack_from('<I', data, 8)[0]
    assert data[:4] == b'glTF' and total == len(data)
    offset, gltf, binary = 12, None, None
    while offset < len(data):
        size, kind = struct.unpack_from('<II', data, offset)
        body = data[offset + 8:offset + 8 + size]
        offset += 8 + size
        if kind == GLB_JSON:
            gltf = json.loads(body.decode('utf-8'))
        elif kind == GLB_BIN:
            binary = body
    assert gltf is not None
    return gltf, binary


def glb_join(gltf, binary):
    """A valid GLB: JSON chunk padded with spaces to 4 bytes, BIN chunk kept, lengths recomputed."""
    body = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    body += b' ' * (-len(body) % 4)
    chunks = struct.pack('<II', len(body), GLB_JSON) + body
    if binary is not None:
        chunks += struct.pack('<II', len(binary), GLB_BIN) + binary
    return struct.pack('<4sII', b'glTF', 2, 12 + len(chunks)) + chunks


def glb_relabelled(data):
    gltf, binary = glb_split(data)
    for key in ('nodes', 'meshes', 'materials', 'scenes'):
        for index, item in enumerate(gltf.get(key, [])):
            item['name'] = f'root_section_{index}'
    return glb_join(gltf, binary)


def glb_scaled(data, factor):
    """A new root node with scale=factor above the original scene roots (works whatever the roots hold)."""
    gltf, binary = glb_split(data)
    scene = gltf['scenes'][gltf.get('scene', 0)]
    gltf['nodes'].append({'name': 'scaler', 'scale': [factor, factor, factor], 'children': list(scene['nodes'])})
    scene['nodes'] = [len(gltf['nodes']) - 1]
    return glb_join(gltf, binary)


def glb_without_meshes(data):
    gltf, binary = glb_split(data)
    for node in gltf['nodes']:
        node.pop('mesh', None)
    return glb_join(gltf, binary)


def step_relabelled(data):
    """Misleading entity names on faces, solids and products; geometry numbers untouched."""
    names = (b'root_section', b'free_end', b'beam.solid', b'wrong_face')
    count = [0]

    def face(_match):
        name = names[count[0] % len(names)]
        count[0] += 1
        return b"ADVANCED_FACE('" + name + b"'"

    out = re.sub(rb"ADVANCED_FACE\('[^']*'", face, data)
    assert count[0] >= 6, 'the STEP has no ADVANCED_FACE entities to relabel'
    out = re.sub(rb"MANIFOLD_SOLID_BREP\('[^']*'", lambda _m: b"MANIFOLD_SOLID_BREP('root_section'", out)
    out = re.sub(rb"PRODUCT\('[^']*',", lambda _m: b"PRODUCT('root_section',", out)
    assert out != data
    return out


def box(length, width, height):
    import cadquery as cq
    return cq.Workplane('XY').box(length, width, height, centered=False)


# --------------------------------------------------------------------------- P07 with the gate on

def test_p07_gated_15mm_is_verified_published_and_bound(gated):
    first, w = gated.first, gated.w
    assert first.state == 'published', first
    run = job_run(w, first.job_id)
    assert [a.candidate.specification.height_mm for a in run.attempts] == [5, 10, 15]
    assert run.outcome.value == 'accepted' and run.selected_candidate_sha256 == gated.spec_of(15.0).candidate_sha256
    record = gated.stored()
    assert record is not None and record.verdict == 'verified', record
    assert gated.calls.count(first.revision_id) == 1
    assert record.candidate_sha256 == run.selected_candidate_sha256
    assert record.manifest_sha256 == first.bundle.manifest_sha256
    assert record.step_sha256 == step_sha(w, first.revision_id)
    row = w.svc.verifier.evidence.get(None, first.revision_id)
    assert row == record
    published = [e for e in w.svc.journal(w.pid, client=CLIENT)
                 if e.event == 'published' and e.revision_id == first.revision_id]
    assert len(published) == 1
    assert published[0].detail == {'job_id': first.job_id, 'evidence_hash': evidence_sha256(row)}
    assert bind_states(record) == {'beam.solid': 'bound', 'beam.root_section': 'bound', 'beam.free_end': 'bound'}
    geometry = [c for c in record.checks if c.category == 'geometry']
    assert geometry and all(c.required and c.state == 'passed' and c.coverage == 'measured' for c in geometry)
    for c in geometry:
        assert c.measurements and c.error_bound is not None and c.error_bound.unit == 'mm'
        assert all(m.representation == 'brep' and m.satisfied for m in c.measurements), c.check_id
    physics = check_of(record, 'physics.report_inputs')
    assert physics.state == 'passed' and physics.coverage == 'policy_report'
    for check_id in ('manufacturing.not_evaluated', 'standards.not_evaluated'):
        assert check_of(record, check_id).state == 'not_evaluated'
        assert check_of(record, check_id).coverage == 'not_evaluated'
    preview = check_of(record, 'delivered.preview_extent')
    assert preview.state == 'not_evaluated' and preview.reason == 'bound_exceeds_tolerance'
    assert not preview.required and preview.coverage == 'not_evaluated'
    assert [(v.name, v.version) for v in record.native_versions] == [
        (v.name, v.version) for v in first.lock.backend_lock.native_versions]


def test_p07_gated_5_10_batch_is_rejected_and_never_verified(gated):
    w, first = gated.w, gated.first
    before = (first.brief_sha256, first.fixed_requirements_sha256, gated.stored())
    calls = list(gated.calls)

    async def scenario():
        rev, brief_id = await stage_final(w, REJECTED, head_id(w))
        return await land_staged(w, rev, brief_id)

    done = gated.run(scenario())
    assert done.state == 'rejected' and done.published_seq is None and done.bundle is None, done
    assert gated.calls == calls, 'the verifier was never called for the rejected batch'
    assert w.svc.evidence(done.revision_id, client=CLIENT) is None
    with w.store.transaction() as db:
        assert w.svc.verifier.evidence.get(db, done.revision_id) is None
    assert done.fixed_requirements_sha256 == first.fixed_requirements_sha256
    current = w.svc.current(w.pid, client=CLIENT)
    assert current.revision.revision_id == first.revision_id and current.artifacts == 'available'
    after = w.svc.view(first.revision_id, client=CLIENT).revision
    assert (after.brief_sha256, after.fixed_requirements_sha256, gated.stored()) == before


# --------------------------------------------------------------------------- T1 / T2: dimensions

def test_t1_ten_mm_box_in_the_15mm_bundle_fails_and_is_not_promoted(gated):
    record, problem = gated.tamper({'model.step': step_of(gated, box(200, 10, 10), 't1')})
    assert record.verdict == 'failed', record.reasons
    assert bind_states(record) == {'beam.solid': 'bound', 'beam.root_section': 'missing',
                                   'beam.free_end': 'missing'}
    assert check_of(record, 'delivered.height').state == 'failed'
    assert check_of(record, 'delivered.volume').state == 'failed'
    assert check_of(record, 'delivered.root_section').state == 'failed'
    assert check_of(record, 'delivered.free_end').state == 'failed'
    assert check_of(record, 'physics.report_inputs').state == 'failed'
    assert 'report_mismatch' in record.reasons and 'limit_exceeded' in record.reasons
    assert problem == DELIVERED_CHECK_FAILED
    assert not any(c.state == 'passed' and c.check_id in (
        'delivered.height', 'delivered.volume', 'delivered.root_section') for c in record.checks)


def test_t2_width_and_height_swapped_fails(gated):
    record, problem = gated.tamper({'model.step': step_of(gated, box(200, 15, 10), 't2swap')})
    assert record.verdict == 'failed'
    # root and free faces are 15 x 10 = 10 x 15 in area: they still bind; the dimensions catch it
    assert bind_states(record) == {'beam.solid': 'bound', 'beam.root_section': 'bound', 'beam.free_end': 'bound'}
    assert check_of(record, 'delivered.width').state == 'failed'
    assert check_of(record, 'delivered.height').state == 'failed'
    assert check_of(record, 'delivered.length').state == 'passed'
    assert problem == DELIVERED_CHECK_FAILED


def test_t2_off_datum_in_x_and_in_z_fails(gated):
    record_x, problem_x = gated.tamper({'model.step': step_of(
        gated, box(200, 10, 15).translate((0.01, 0, 0)), 't2x')})
    assert record_x.verdict == 'failed' and problem_x == DELIVERED_CHECK_FAILED
    assert bind_states(record_x)['beam.root_section'] == 'missing'
    assert check_of(record_x, 'delivered.root_section').state == 'failed'
    record_z, problem_z = gated.tamper({'model.step': step_of(
        gated, box(200, 10, 15).translate((0, 0, 0.01)), 't2z')})
    assert record_z.verdict == 'failed' and problem_z == DELIVERED_CHECK_FAILED
    assert bind_states(record_z) == {'beam.solid': 'bound', 'beam.root_section': 'bound', 'beam.free_end': 'bound'}
    assert check_of(record_z, 'delivered.datum').state == 'failed'
    assert check_of(record_z, 'delivered.height').state == 'passed'


def test_t2_length_just_past_tolerance_fails(gated):
    record, problem = gated.tamper({'model.step': step_of(gated, box(200.011, 10, 15), 't2len')})
    assert record.verdict == 'failed' and problem == DELIVERED_CHECK_FAILED
    assert check_of(record, 'delivered.length').state == 'failed'
    assert check_of(record, 'delivered.volume').state == 'failed'
    assert bind_states(record)['beam.free_end'] == 'missing'
    assert bind_states(record)['beam.root_section'] == 'bound'


# --------------------------------------------------------------------------- T3: features

def test_t3_through_hole_fails_on_volume(gated):
    import cadquery as cq
    cutter = cq.Workplane('XY').center(100, 5).circle(2).extrude(17).translate((0, 0, -1))
    shape = box(200, 10, 15).cut(cutter)
    assert len(shape.faces().vals()) >= 7
    record, problem = gated.tamper({'model.step': step_of(gated, shape, 't3hole')})
    assert record.verdict == 'failed' and problem == DELIVERED_CHECK_FAILED
    assert bind_states(record) == {'beam.solid': 'bound', 'beam.root_section': 'bound', 'beam.free_end': 'bound'}
    assert check_of(record, 'delivered.volume').state == 'failed'


def test_t3_chamfered_free_end_edge_loses_the_free_end_binding(gated):
    shape = box(200, 10, 15).faces('>X').edges('>Z').chamfer(1.0)
    record, problem = gated.tamper({'model.step': step_of(gated, shape, 't3chamfer')})
    assert record.verdict == 'failed' and problem == DELIVERED_CHECK_FAILED
    assert bind_states(record) == {'beam.solid': 'bound', 'beam.root_section': 'bound', 'beam.free_end': 'missing'}
    assert check_of(record, 'delivered.free_end').state == 'failed'
    assert check_of(record, 'delivered.volume').state == 'failed'


def test_t3_two_solids_are_ambiguous(gated):
    import cadquery as cq
    compound = cq.Compound.makeCompound([box(200, 10, 15).val(), box(200, 10, 15).translate((0, 20, 0)).val()])
    shape = cq.Workplane('XY').newObject([compound])
    record, problem = gated.tamper({'model.step': step_of(gated, shape, 't3two')})
    assert record.verdict == 'ambiguous' and problem == DELIVERED_BINDING_AMBIGUOUS
    assert bind_states(record) == {'beam.solid': 'ambiguous', 'beam.root_section': 'ambiguous',
                                   'beam.free_end': 'ambiguous'}
    assert next(b for b in record.bindings if b.feature == 'beam.solid').match_count == 2
    assert not any(c.state == 'passed' and c.check_id == 'physics.report_inputs' for c in record.checks)


def test_t3_split_root_face_is_missing(gated):
    half = box(200, 5, 15)
    shape = half.union(box(200, 5, 15).translate((0, 5, 0)), clean=False)
    assert len(shape.faces().vals()) > 6, 'the fused halves must keep their split faces'
    record, problem = gated.tamper({'model.step': step_of(gated, shape, 't3split')})
    assert record.verdict == 'failed' and problem == DELIVERED_CHECK_FAILED
    assert bind_states(record) == {'beam.solid': 'bound', 'beam.root_section': 'missing',
                                   'beam.free_end': 'missing'}
    assert check_of(record, 'delivered.root_section').state == 'failed'
    assert check_of(record, 'delivered.volume').state == 'passed'


# --------------------------------------------------------------------------- T4: relabel only

def test_t4_relabelled_step_and_glb_names_stay_verified_with_equal_signatures(gated):
    base = gated.stored()
    assert base.verdict == 'verified'
    original = gated.originals
    cases = {
        'step': {'model.step': step_relabelled(original['model.step'])},
        'glb': {'preview.glb': glb_relabelled(original['preview.glb'])},
        'both': {'model.step': step_relabelled(original['model.step']),
                 'preview.glb': glb_relabelled(original['preview.glb'])},
    }
    for name, replacements in cases.items():
        for key, value in replacements.items():
            assert value != original[key], (name, key)
        record, problem = gated.tamper(replacements)
        assert record.verdict == 'verified', (name, record.reasons)
        assert problem is None, name
        assert record.bindings == base.bindings, name  # same face signatures: names are never read
        assert record.checks == base.checks, name
        assert record.step_sha256 != base.step_sha256 or name == 'glb'
        assert record.glb_sha256 != base.glb_sha256 or name == 'step'


# --------------------------------------------------------------------------- T5: report, spec, attempts

def test_t5_report_physics_inputs_altered_fail_with_report_mismatch(gated):
    selected = gated.spec_of(15.0).candidate_sha256
    for field, change in (('section_iy_mm4', lambda v: v * 2.0), ('length_mm', lambda v: v + 1.0),
                          ('height_mm', lambda v: v * 2.0)):
        report = json.loads(gated.originals['report.json'])
        evaluation = next(e for e in report['evaluations'] if e['candidate_sha256'] == selected)
        measured = evaluation['native_report']['measurements']
        measured[field] = change(measured[field])
        record, problem = gated.tamper({'report.json': encode(report)})
        assert record.verdict == 'failed' and problem == DELIVERED_CHECK_FAILED, field
        assert 'report_mismatch' in record.reasons, field
        physics = check_of(record, 'physics.report_inputs')
        assert physics.state == 'failed' and physics.reason == 'report_mismatch', field
    report = json.loads(gated.originals['report.json'])
    evaluation = next(e for e in report['evaluations'] if e['candidate_sha256'] == selected)
    evaluation['native_report']['physics']['state'] = 'failed'
    record, problem = gated.tamper({'report.json': encode(report)})
    assert record.verdict == 'failed' and record.reasons == ('report_mismatch',)
    assert problem == DELIVERED_CHECK_FAILED


def test_t5_selected_spec_swapped_to_the_10mm_candidate_fails(gated):
    other = gated.spec_of(10.0).specification.model_dump(mode='json')
    record, problem = gated.tamper({'selected_spec.json': encode(other)})
    assert record.verdict == 'failed' and record.reasons == ('report_mismatch',)
    assert record.candidate_sha256 == gated.spec_of(15.0).candidate_sha256
    assert problem == DELIVERED_CHECK_FAILED


def test_t5_attempts_altered_fail_with_report_mismatch(gated):
    attempts = json.loads(gated.originals['attempts.json'])
    attempts[-1]['outcome'] = 'rejected'
    record, problem = gated.tamper({'attempts.json': encode(attempts)})
    assert record.verdict == 'failed' and record.reasons == ('report_mismatch',)
    assert problem == DELIVERED_CHECK_FAILED
    attempts = json.loads(gated.originals['attempts.json'])
    attempts[0]['outcome'] = 'accepted'
    record, problem = gated.tamper({'attempts.json': encode(attempts)})
    assert record.verdict == 'failed' and 'report_mismatch' in record.reasons


# --------------------------------------------------------------------------- T6: preview

def test_t6_substituted_scaled_or_empty_preview_fails_and_the_exact_one_does_not_pass(gated):
    exact = check_of(gated.stored(), 'delivered.preview_extent')
    assert exact.state == 'not_evaluated' and exact.reason == 'bound_exceeds_tolerance'
    original = gated.originals['preview.glb']
    cases = {
        'substituted': glb_of(gated, box(200, 10, 10), 't6sub'),
        'scaled_2': glb_scaled(original, 2.0),
        'scaled_1000': glb_scaled(original, 1000.0),
        'no_triangles': glb_without_meshes(original),
        'unparsable': b'this is not a glb file',
    }
    for name, data in cases.items():
        assert data != original, name
        record, problem = gated.tamper({'preview.glb': data})
        assert record.verdict == 'failed', (name, record.reasons)
        assert problem == DELIVERED_CHECK_FAILED, name
        preview = check_of(record, 'delivered.preview_extent')
        assert preview.state == 'failed' and preview.coverage == 'measured', name
        assert preview.reason == 'limit_exceeded', name
        # the STEP and the physics inputs are untouched: only the preview is wrong
        assert bind_states(record) == {'beam.solid': 'bound', 'beam.root_section': 'bound',
                                       'beam.free_end': 'bound'}, name
        assert check_of(record, 'delivered.height').state == 'passed', name


# --------------------------------------------------------------------------- the world is restored

def test_tamper_cases_leave_the_published_bytes_and_rev1_evidence_unchanged(gated):
    directory = gated.w.pub.directory(gated.first.job_id)
    for name, data in gated.originals.items():
        assert (directory / name).read_bytes() == data, name
    assert gated.stored().verdict == 'verified'
    assert gated.w.svc.current(gated.w.pid, client=CLIENT).revision.revision_id == gated.first.revision_id

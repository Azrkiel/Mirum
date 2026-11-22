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
